"""Module for image segmentation and feature extraction in high-content screening workflows using OMERO.

This module provides tools to segment microscopy images with nucleus and cell channels, apply flatfield correction,
and extract quantitative properties from labelled regions. It is designed to work with OMERO server objects and
supports multi-channel, multi-timepoint images. The segmentation leverages Cellpose models, and extracted features
are organized into pandas DataFrames for downstream analysis.

Main Components:
----------------
- Image: Handles image correction, segmentation (nucleus, cell, cytoplasm), and mask management. Integrates with OMERO objects and supports flatfield correction.
- ImageProperties: Extracts region properties (area, intensity, etc.) from segmented masks, merges features across channels, and compiles experiment metadata.

Key Features:
-------------
- Flatfield correction for each channel using provided masks.
- Segmentation using Cellpose models, with model selection based on cell line and magnification.
- Automatic mask upload and retrieval from OMERO datasets.
- Extraction of region properties (area, intensity, etc.) for nuclei, cells, and cytoplasm.
- Data organization into pandas DataFrames, including experiment and well metadata.
- Quality control metrics for each image channel.
"""

import logging
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from cellpose import models
from ezomero import get_image
from omero.gateway import BlitzGateway, ImageWrapper, WellWrapper
from omero_utils.images import parse_mip, upload_masks
from pandas.api.types import is_integer_dtype
from skimage import measure

from omero_screen import default_config
from omero_screen.general_functions import filter_segmentation, scale_img
from omero_screen.image_classifier import ImageClassifier
from omero_screen.metadata_parser import MetadataParser
from omero_screen.torch import get_device

logger = logging.getLogger("omero-screen")


class Image:
    """Generates corrected images and segmentation masks for microscopy data.

    This class handles flatfield correction, segmentation of nuclei and cell channels using Cellpose models, and management of segmentation masks.
    It stores corrected images and segmentation results for downstream analysis.

    Attributes:
        img_dict (dict[str, np.ndarray]): Dictionary mapping channel names to flatfield-corrected image arrays.
        n_mask (np.ndarray): Segmentation mask for nuclei.
        c_mask (np.ndarray or None): Segmentation mask for cells, if available.
        cyto_mask (np.ndarray or None): Segmentation mask for cytoplasm, if available.
        nuc_diameter (int): Estimated diameter of nuclei, used for segmentation.
        channels (dict): Channel metadata from the MetadataParser.
        well_pos (tuple): Well position in the plate.
        cell_line (str): Cell line name for the current well.

    Args:
        conn (BlitzGateway): OMERO server connection.
        well (WellWrapper): OMERO WellWrapper object for the current well.
        image_obj (ImageWrapper): OMERO ImageWrapper object for the image.
        metadata (MetadataParser): Metadata parser with channel and plate information.
        dataset_id (int): OMERO dataset ID.
        flatfield_dict (dict[str, np.ndarray]): Flatfield correction masks for each channel.
    """

    def __init__(
        self,
        conn: BlitzGateway,
        well: WellWrapper,
        image_obj: ImageWrapper,
        metadata: MetadataParser,
        dataset_id: int,
        flatfield_dict: dict[str, npt.NDArray[Any]],
    ):
        """Initializes the Image object for segmentation and correction.

        Args:
            conn (BlitzGateway): OMERO server connection.
            well (WellWrapper): OMERO WellWrapper object for the current well.
            image_obj (ImageWrapper): OMERO ImageWrapper object for the image.
            metadata (MetadataParser): Metadata parser with channel and plate information.
            dataset_id (int): OMERO dataset ID.
            flatfield_dict (dict[str, np.ndarray]): Flatfield correction masks for each channel.
        """
        self._conn = conn
        self._well = well
        self.omero_image = image_obj
        self._meta_data = metadata
        self.dataset_id = dataset_id
        self._flatfield_dict = flatfield_dict

        self._get_metadata()
        self.nuc_diameter = (
            10  # default value for nuclei diameter for 10x images
        )
        self.img_dict = self._get_img_dict()
        self.n_mask, self.c_mask, self.cyto_mask = self._segmentation()

    def _get_metadata(self) -> None:
        """Extracts channel metadata, well position, and cell line information from the metadata parser."""
        self.channels = self._meta_data.channel_data
        self.well_pos = self._well.getWellPos()
        self.cell_line = self._meta_data.well_conditions(self.well_pos)[
            "cell_line"
        ]

    def _get_img_dict(self) -> dict[str, npt.NDArray[Any]]:
        """Divide image_array with flatfield correction mask and return dictionary "channel_name": corrected image.

        Returns:
            dict[str, npt.NDArray[Any]]: Dictionary mapping channel names to flatfield-corrected image arrays.
        """
        img_dict = {}
        image_id = self.omero_image.getId()
        if self.omero_image.getSizeZ() > 1:
            array = parse_mip(self._conn, image_id, self.dataset_id)
        else:
            _, array = get_image(self._conn, image_id)

        for ch, idx in self.channels.items():
            img = array[..., int(idx)] / self._flatfield_dict[ch]
            # Reduce (tzyx) to (tyx)
            img = np.squeeze(img, axis=1)

            # # Convert back to original pixel type, clipping as necessary.
            # np.clip(img, out=img, a_min=0, a_max=np.iinfo(array.dtype).max)
            # img_dict[ch] = img.astype(array.dtype)

            # Use float image. When passed to scale_img this will scale to [0, 1] for cellpose.
            img_dict[ch] = img
        return img_dict

    def _segmentation(
        self,
    ) -> tuple[
        npt.NDArray[Any], npt.NDArray[Any] | None, npt.NDArray[Any] | None
    ]:
        """Performs segmentation of nuclei and cell channels, retrieving or generating masks as needed.

        This method checks if segmentation masks already exist in the OMERO dataset. If not, it performs segmentation using Cellpose models,
        generates the required masks, and uploads them to OMERO. It supports both nucleus-only and nucleus+cell segmentation workflows.

        Returns:
            tuple:
                n_mask (np.ndarray): Segmentation mask for nuclei.
                c_mask (np.ndarray or None): Segmentation mask for cells, if available.
                cyto_mask (np.ndarray or None): Segmentation mask for cytoplasm, if available.
        """
        # check if masks already exist
        image_name = f"{self.omero_image.getId()}_segmentation"
        dataset = self._conn.getObject("Dataset", self.dataset_id)
        n_mask, c_mask, cyto_mask = None, None, None
        for image in dataset.listChildren():
            if image.getName() == image_name:
                image_id = image.getId()
                logger.info("Segmentation masks found for image %s", image_id)
                # masks is TZYXC
                _, masks = get_image(self._conn, image_id)
                if "Tub" in self.channels:
                    n_mask, c_mask = masks[..., 0], masks[..., 1]
                    cyto_mask = self._get_cyto(n_mask, c_mask)
                else:
                    n_mask = masks[..., 0]
                break  # stop the loop once the image is found
        if n_mask is None:
            n_mask = self._n_segmentation()
            if "Tub" in self.channels:
                c_mask = self._c_segmentation()
                n_mask, c_mask = self._compact_mask(np.stack([n_mask, c_mask]))
                cyto_mask = self._get_cyto(n_mask, c_mask)
            else:
                n_mask = self._compact_mask(n_mask)

            upload_masks(
                self._conn,
                self.dataset_id,
                self.omero_image,
                n_mask,
                c_mask,
            )
        return n_mask, c_mask, cyto_mask

    def _get_cyto(
        self, n_mask: npt.NDArray[Any], c_mask: npt.NDArray[Any]
    ) -> npt.NDArray[Any] | None:
        """Substract nuclei mask from cell mask to get cytoplasm mask.

        Args:
            n_mask (npt.NDArray[Any]): Nuclei segmentation mask.
            c_mask (npt.NDArray[Any]): Cell segmentation mask.

        Returns:
            npt.NDArray[Any] | None: Cytoplasm segmentation mask.
        """
        overlap = (c_mask != 0) * (n_mask != 0)
        cyto_mask_binary = (c_mask != 0) * (overlap == 0)
        return c_mask * cyto_mask_binary  # type: ignore[no-any-return]

    def _n_segmentation(self) -> npt.NDArray[Any]:
        """Performs nuclei segmentation using Cellpose models.

        This method selects the appropriate Cellpose model based on the cell line and magnification,
        and performs segmentation on the DAPI channel.

        Returns:
            npt.NDArray[Any]: Segmentation mask for nuclei.
        """
        if "40X" in self.cell_line.upper():
            self.nuc_diameter = 100
        elif "20X" in self.cell_line.upper():
            self.nuc_diameter = 25
        else:
            self.nuc_diameter = 10

        segmentation_model = models.CellposeModel(
            device=get_device(),
            model_type=default_config.MODEL_DICT["nuclei"],
        )
        # Get the image array
        img_array = self.img_dict["DAPI"]

        # Initialize an array to store the segmentation masks
        segmentation_masks = np.zeros_like(img_array, dtype=np.uint32)

        for t in range(img_array.shape[0]):
            # Select the image at the current timepoint
            img_t = img_array[t]

            # Prepare the image for segmentation
            scaled_img_t = scale_img(img_t)

            # Perform segmentation
            n_channels = [[0, 0]]
            logger.info(
                "Segmenting nuclei with diameter %s", self.nuc_diameter
            )
            try:
                n_mask_array, n_flows, n_styles = segmentation_model.eval(
                    scaled_img_t,
                    channels=n_channels,
                    diameter=self.nuc_diameter,
                    normalize=False,
                )
            except IndexError:
                n_mask_array = np.zeros(scaled_img_t.shape, dtype=np.uint8)
            # Store the segmentation mask in the corresponding timepoint
            segmentation_masks[t] = filter_segmentation(n_mask_array)
        return segmentation_masks

    def _c_segmentation(self) -> npt.NDArray[Any]:
        """Perform cellpose segmentation using cell mask.

        This method uses the CellposeModel to segment the cell channel.

        Returns:
            npt.NDArray[Any]: Segmentation mask for cells.
        """
        segmentation_model = models.CellposeModel(
            device=get_device(),
            model_type=self._get_models(),
        )
        c_channels = [[2, 1]]

        # Get the image arrays for DAPI and Tubulin channels
        dapi_array = self.img_dict["DAPI"]
        tub_array = self.img_dict["Tub"]

        # Check if the time dimension matches
        assert dapi_array.shape[0] == tub_array.shape[0], (
            "Time dimension mismatch between DAPI and Tubulin channels"
        )

        # Initialize an array to store the segmentation masks
        segmentation_masks = np.zeros_like(dapi_array, dtype=np.uint32)

        # Process each timepoint
        for t in range(dapi_array.shape[0]):
            # Select the images at the current timepoint
            dapi_t = dapi_array[t]
            tub_t = tub_array[t]

            # Combine the 2 channel numpy array for cell segmentation with the nuclei channel
            comb_image_t = scale_img(np.dstack([dapi_t, tub_t]))

            # Perform segmentation
            try:
                c_masks_array, c_flows, c_styles = segmentation_model.eval(
                    comb_image_t, channels=c_channels, normalize=False
                )
            except IndexError:
                c_masks_array = np.zeros_like(comb_image_t).astype(np.uint8)

            # Store the segmentation mask in the corresponding timepoint
            segmentation_masks[t] = filter_segmentation(c_masks_array)
        return segmentation_masks

    def _get_models(self) -> str:
        """Matches well with cell line and gets model_path for cell line from plate_layout.

        Returns:
            path to model
        """
        cell_line = self.cell_line.replace(
            " ", ""
        ).upper()  # remove spaces and make uppercase
        if "40X" in cell_line:
            logger.info("40x image detected, using 40x nuclei model")
            return "40x_Tub_H2B"
        elif "20X" in cell_line:
            logger.info("20x image detected, using 20x nuclei model")
            return "cyto"
        elif cell_line in default_config.MODEL_DICT:
            return default_config.MODEL_DICT[cell_line]
        else:
            return default_config.MODEL_DICT["U2OS"]

    def _compact_mask(self, mask: npt.NDArray[Any]) -> npt.NDArray[Any]:
        """Compact the uint32 datatype to the smallest required to store all mask IDs.

        Args:
            mask (npt.NDArray[Any]): Segmentation mask.

        Returns:
            npt.NDArray[Any]: Compact segmentation mask.
        """
        m = mask.max()
        if m < 2**8:
            return mask.astype(np.uint8)
        if m < 2**16:
            return mask.astype(np.uint16)
        return mask


class ImageProperties:
    """Extracts feature measurements from segmented nuclei, cells and cytoplasm and generates combined data frames.

    This class processes segmented masks to extract quantitative features from nuclei, cells, and cytoplasm.
    It combines measurements from different channels and generates a comprehensive DataFrame for downstream analysis.

    Attributes:
        image_df (pd.DataFrame): DataFrame containing feature measurements for all regions and channels.
        quality_df (pd.DataFrame): DataFrame containing quality control metrics for each channel.
        plate_name (str): Name of the plate.
        _cond_dict (dict): Experimental conditions for the current well.
        _well (WellWrapper): OMERO WellWrapper object for the current well.
        _well_id (int): OMERO Well ID.
        _image (Image): Image object containing segmentation masks and corrected images.
        _meta_data (MetadataParser): Metadata parser with channel and plate information.
        _overlay (pd.DataFrame): DataFrame linking nuclear IDs with cell IDs.
    """

    def __init__(
        self,
        well: WellWrapper,
        image_obj: Image,
        meta_data: MetadataParser,
        featurelist: list[str] = default_config.FEATURELIST,
        image_classifier: None | list[ImageClassifier] = None,
    ):
        """Initializes the ImageProperties object for feature extraction and data aggregation.

        Args:
            well (WellWrapper): OMERO WellWrapper object for the current well.
            image_obj (Image): Image object containing segmentation masks and corrected images.
            meta_data (MetadataParser): Metadata parser with channel and plate information.
            featurelist (list[str], optional): List of features to extract from segmented regions. Defaults to default_config.FEATURELIST.
            image_classifier (optional): Optional image classifier(s) for additional processing. Defaults to None.
        """
        self._well = well
        self._well_id = well.getId()
        self._image = image_obj
        self._meta_data = meta_data

        # Assumes the well parent is the plate
        self.plate_name = well.getParent().getName()
        # Get the dict[str, Any] for the given well
        self._cond_dict = meta_data.well_conditions(well.getWellPos())
        self._overlay = self._overlay_mask()
        self.image_df = self._combine_channels(featurelist)
        self.quality_df = self._concat_quality_df()

        if image_classifier is not None and image_obj.c_mask is not None:
            for cls in image_classifier:
                if cls.select_channels(image_obj.img_dict):
                    self.image_df = cls.process_images(
                        self.image_df, image_obj.c_mask
                    )

    def _overlay_mask(self) -> pd.DataFrame:
        """Links nuclear IDs with cell IDs.

        This method creates a DataFrame linking nuclear IDs with cell IDs.

        Returns:
            pd.DataFrame: DataFrame linking nuclear IDs with cell IDs.
        """
        if self._image.c_mask is None:
            return pd.DataFrame({"label": self._image.n_mask.flatten()})

        overlap = (self._image.c_mask != 0) * (self._image.n_mask != 0)
        stack = np.stack(
            [self._image.n_mask[overlap], self._image.c_mask[overlap]]
        )
        list_n_masks = stack[-2].tolist()
        list_masks = stack[-1].tolist()
        overlay_all = {
            list_n_masks[i]: list_masks[i] for i in range(len(list_n_masks))
        }
        return pd.DataFrame(
            list(overlay_all.items()), columns=["label", "Cyto_ID"]
        )

    def _combine_channels(self, featurelist: list[str]) -> pd.DataFrame:
        """Combines feature measurements from different channels into a single DataFrame.

        This method processes the segmented masks for each channel and combines the measurements into a single DataFrame.

        Returns:
            pd.DataFrame: DataFrame containing feature measurements for all regions and channels.
        """
        channel_data = [
            self._channel_data(channel, featurelist)
            for channel in self._meta_data.channel_data
        ]
        props_data = pd.concat(channel_data, axis=1, join="inner")
        edited_props_data = props_data.loc[
            :, ~props_data.columns.duplicated()
        ].copy()
        cond_list = [
            self.plate_name,
            self._meta_data.plate_id,
            self._well.getWellPos(),
            self._well_id,
            self._image.omero_image.getId(),
        ]
        cond_list.extend(iter(self._cond_dict.values()))
        col_list = ["experiment", "plate_id", "well", "well_id", "image_id"]
        col_list.extend(iter(self._cond_dict.keys()))
        col_list_edited = [entry.lower() for entry in col_list]
        edited_props_data[col_list_edited] = cond_list

        return edited_props_data.sort_values(by=["timepoint"]).reset_index(
            drop=True
        )

    def _channel_data(
        self, channel: str, featurelist: list[str]
    ) -> pd.DataFrame:
        """Processes the segmented masks for a specific channel and combines the measurements into a single DataFrame.

        This method extracts quantitative features from the segmented masks for a given channel and combines them with the overlay DataFrame.

        Returns:
            pd.DataFrame: DataFrame containing feature measurements for the given channel.
        """
        nucleus_data = self._get_properties(
            self._image.n_mask, channel, "nucleus", featurelist
        )
        # merge channel data, outer merge combines all area columns into 1
        if self._image.c_mask is not None:
            nucleus_data = self._outer_merge(
                nucleus_data, self._overlay, "label"
            )
        if channel == "DAPI":
            nucleus_data["integrated_int_DAPI"] = (
                nucleus_data["intensity_mean_DAPI_nucleus"]
                * nucleus_data["area_nucleus"]
            )

        if (
            self._image.c_mask is not None
            and self._image.cyto_mask is not None
        ):
            cell_data = self._get_properties(
                self._image.c_mask, channel, "cell", featurelist
            )
            cyto_data = self._get_properties(
                self._image.cyto_mask, channel, "cyto", featurelist
            )
            merge_1 = self._outer_merge(
                cell_data, cyto_data, ["label", "timepoint"]
            )
            merge_1 = merge_1.rename(columns={"label": "Cyto_ID"})
            return self._outer_merge(
                nucleus_data, merge_1, ["Cyto_ID", "timepoint"]
            )
        else:
            return nucleus_data

    def _get_properties(
        self,
        segmentation_mask: npt.NDArray[Any],
        channel: str,
        segment: str,
        featurelist: list[str],
    ) -> pd.DataFrame:
        """Measure selected features for each segmented cell in given channel.

        This method measures the selected features for each segmented cell in the given channel.

        Returns:
            pd.DataFrame: DataFrame containing feature measurements for the given channel.
        """
        timepoints = self._image.img_dict[channel].shape[0]
        # squeezing [t]z
        label = np.squeeze(segmentation_mask).astype(np.int64)

        if timepoints > 1:
            data_list = []
            for t in range(timepoints):
                props = measure.regionprops_table(  # type: ignore[no-untyped-call]
                    label[t],
                    # squeezing z
                    np.squeeze(self._image.img_dict[channel][t]),
                    properties=featurelist,
                )
                data = pd.DataFrame(props)
                feature_dict = self._edit_properties(
                    channel, segment, featurelist
                )
                data = data.rename(columns=feature_dict)
                data["timepoint"] = t  # Add timepoint for all channels
                data_list.append(data)
            combined_data = pd.concat(data_list, axis=0, ignore_index=True)
            return combined_data.sort_values(
                by=["timepoint", "label"]
            ).reset_index(drop=True)
        else:
            props = measure.regionprops_table(  # type: ignore[no-untyped-call]
                label,
                # squeezing tz
                np.squeeze(self._image.img_dict[channel]),
                properties=featurelist,
            )
            data = pd.DataFrame(props)
            feature_dict = self._edit_properties(channel, segment, featurelist)
            data = data.rename(columns=feature_dict)
            data["timepoint"] = 0  # Add timepoint 0 for single timepoint data
            return data.sort_values(by=["label"]).reset_index(drop=True)

    @staticmethod
    def _edit_properties(
        channel: str, segment: str, featurelist: list[str]
    ) -> dict[str, str]:
        """Edit the properties of the features.

        This method edits the properties of the features to be used in the DataFrame.

        Returns:
            dict[str, str]: Dictionary mapping feature names to their edited names.
        """
        feature_dict = {
            feature: f"{feature}_{channel}_{segment}"
            for feature in featurelist[2:]
        }
        feature_dict["area"] = (
            f"area_{segment}"  # the area is the same for each channel
        )
        return feature_dict

    def _outer_merge(
        self, df1: pd.DataFrame, df2: pd.DataFrame, on: list[str] | str
    ) -> pd.DataFrame:
        """Perform an outer-join merge on the two pandas dataframes. NA rows are removed and integer columns are restored.

        This method performs an outer-join merge on the two pandas DataFrames and removes NA rows.

        Returns:
            pd.DataFrame: Merged DataFrame with integer columns restored.
        """
        df = pd.merge(df1, df2, how="outer", on=on).dropna(axis=0, how="any")
        # Outer-join merge will create columns that support NA. This changes int columns to float.
        # After dropping all the NA rows restore the int columns.
        for c in df1.columns:
            if is_integer_dtype(df1[c].dtype) and not is_integer_dtype(
                df[c].dtype
            ):
                df[c] = df[c].astype(df1[c].dtype)
        for c in df2.columns:
            if is_integer_dtype(df2[c].dtype) and not is_integer_dtype(
                df[c].dtype
            ):
                df[c] = df[c].astype(df2[c].dtype)
        return df

    def _set_quality_df(
        self, channel: str, corr_img: npt.NDArray[Any]
    ) -> pd.DataFrame:
        """Generates df for image quality control saving the median intensity of the image.

        This method generates a DataFrame for image quality control by saving the median intensity of the image.

        Returns:
            pd.DataFrame: DataFrame containing quality control metrics for the given channel.
        """
        return pd.DataFrame(
            {
                "experiment": [self.plate_name],
                "plate_id": [self._meta_data.plate_id],
                "position": [self._image.well_pos],
                "image_id": [self._image.omero_image.getId()],
                "channel": [channel],
                "intensity_median": [np.median(corr_img)],
            }
        )

    def _concat_quality_df(self) -> pd.DataFrame:
        """Concatenate quality dfs for all channels in _corr_img_dict.

        This method concatenates the quality DataFrames for all channels in the _corr_img_dict.

        Returns:
            pd.DataFrame: Concatenated DataFrame containing quality control metrics for all channels.
        """
        df_list = [
            self._set_quality_df(channel, image)
            for channel, image in self._image.img_dict.items()
        ]
        return pd.concat(df_list)
