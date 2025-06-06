"""Module for importing single cell measurements into CellView.

This module provides a class for managing single cell measurements import operations
to populate the measurements table.
"""

import duckdb
import pandas as pd
from omero_screen.config import get_logger
from rich.console import Console

from cellview.utils.error_classes import MeasurementError
from cellview.utils.state import CellViewState

# Initialize logger with the module's name
logger = get_logger(__name__)


class MeasurementsManager:
    """Class for managing single cell measurements import operations.

    Attributes:
        db_conn: The DuckDB connection.
        console: The console.
        state: The CellView state.
        logger: The logger.
    """

    def __init__(self, db_conn: duckdb.DuckDBPyConnection) -> None:
        """Initialize the MeasurementsManager.

        Args:
            db_conn: The DuckDB connection.
        """
        self.db_conn: duckdb.DuckDBPyConnection = db_conn
        self.console = Console()
        self.state = CellViewState.get_instance()
        self.logger = get_logger(__name__)

    def import_measurements(self) -> None:
        """Import measurements from the state dataframe into the database.

        Raises:
            MeasurementError: If the state is not valid.
        """
        self.state.prepare_for_measurements()
        # Validate state
        self._validate_state()
        # We know df is not None because _validate_state was called
        assert self.state.df is not None
        assert self.state.condition_id_map is not None

        # Get measurement columns
        measurement_cols = self._get_measurement_columns(self.state.df)

        # Prepare and insert measurements
        self._bulk_insert_measurements(measurement_cols)

        self.console.print("[green]Successfully imported measurements[/green]")

    def _validate_state(self) -> None:
        """Validate that the state has the required data.

        Raises:
            MeasurementError: If the state is not valid.
        """
        if self.state.df is None:
            raise MeasurementError("No data available in state")
        if not self.state.condition_id_map:
            raise MeasurementError("No condition_id map available in state")

    def _get_measurement_columns(self, df: pd.DataFrame) -> list[str]:
        """Get the list of columns to insert into the measurements table.

        Excludes well column as it is used for condition_id lookup but not stored
        in the measurements table. Both image_id and timepoint are required columns
        in the measurements table.

        Args:
            df: The dataframe to get the measurement columns from.

        Returns:
            The list of measurement columns.
        """
        return [col for col in df.columns if col != "well"]

    def _bulk_insert_measurements(self, measurement_cols: list[str]) -> None:
        """Bulk insert measurements into the database using DuckDB's COPY command.

        Args:
            measurement_cols: List of measurement columns to insert

        Raises:
            MeasurementError: If any wells don't have corresponding condition_ids
        """
        # Ensure DataFrame and condition_id_map exist
        if self.state.df is None:
            raise MeasurementError("No DataFrame available in state")
        if self.state.condition_id_map is None:
            raise MeasurementError("No condition_id_map available in state")
        self.logger.debug("measurment_cols: %s", measurement_cols)
        # Add condition_id to the state's DataFrame
        self.state.df["condition_id"] = self.state.df["well"].map(
            self.state.condition_id_map
        )

        # Check for any NaN values in condition_id
        if self.state.df["condition_id"].isna().any():
            missing_wells = (
                self.state.df[self.state.df["condition_id"].isna()]["well"]
                .unique()
                .tolist()
            )
            raise MeasurementError(
                "Found wells without corresponding condition_ids",
                context={
                    "missing_wells": missing_wells,
                    "available_wells": list(
                        self.state.condition_id_map.keys()
                    ),
                },
            )

        # Reorder columns to match database schema
        columns = ["condition_id"] + measurement_cols
        self.state.df = self.state.df[columns]

        # Convert label column to string representation
        if "label" in self.state.df.columns:
            self.state.df["label"] = self.state.df["label"].astype(str)

        self.logger.info("df columns: %s", self.state.df.columns)
        # Bulk insert using DuckDB's COPY FROM
        # Register the DataFrame as a DuckDB table
        try:
            self.db_conn.register("temp_df", self.state.df)
            sql_columns = ", ".join(
                f'"{col}"' for col in self.state.df.columns
            )
            query = f"""
                INSERT INTO measurements ({sql_columns})
                SELECT {sql_columns} FROM temp_df
            """
            self.db_conn.execute(query)

        except Exception as err:
            raise MeasurementError(
                "Failed to import measurements into database"
            ) from err


def import_measurements(conn: duckdb.DuckDBPyConnection) -> None:
    """Instantiate a MeasurementsManager and import measurements.

    Args:
        conn: The DuckDB connection.
    """
    measurements_manager = MeasurementsManager(conn)
    measurements_manager.import_measurements()
