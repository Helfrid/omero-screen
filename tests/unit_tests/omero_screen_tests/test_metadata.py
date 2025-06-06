import pandas as pd
import pytest

from omero.gateway import PlateWrapper
from omero_screen.metadata_parser import (
    ChannelAnnotationError,
    ExcelParsingError,
    MetadataParser,
    PlateNotFoundError,
    WellAnnotationError,
)


def test_plate_validation_failure(omero_conn):
    """Test that plate validation raises an exception when the plate doesn't exist."""
    # Create a string buffer to capture the output
    # Attempt to create a parser with an invalid plate ID
    with pytest.raises(PlateNotFoundError) as exc_info:
        MetadataParser(omero_conn, 5000)
    # Test the exception message
    assert str(exc_info.value) == "A plate with id 5000 was not found!"


def test_plate_validation_success(base_plate):
    """Test that plate validation works correctly when the plate exists."""
    # Arrange
    plate_id = base_plate.getId()
    conn = base_plate._conn

    # Act - creating the parser should validate the plate
    parser = MetadataParser(conn, plate_id)

    # Assert
    assert parser.plate_id == plate_id, (
        "Plate ID should match the input ID"
    )


def test_excel_file_check_no_excel(base_plate):
    """Test that None is returned when no Excel file is present."""
    plate_id = base_plate.getId()
    conn = base_plate._conn
    parser = MetadataParser(conn, plate_id)
    assert parser._check_excel_file() is None


def test_excel_file_check_success(base_plate, attach_excel):
    """Test successful Excel file check with single file"""
    # Variable used implicitly by the test fixture, ignore unused warning
    # ruff: noqa: F841
    file1 = attach_excel(
        base_plate, {"Sheet1": pd.DataFrame({"A": [1, 2], "B": [3, 4]})}
    )
    plate_id = base_plate.getId()
    conn = base_plate._conn
    parser = MetadataParser(conn, plate_id)
    assert parser._check_excel_file() == file1


def test_load_data_from_excel(base_plate, attach_excel, standard_excel_data):
    """Test successful loading of data from Excel file."""
    # ruff: noqa: F841
    file1 = attach_excel(base_plate, standard_excel_data)
    plate_id = base_plate.getId()
    conn = base_plate._conn
    parser = MetadataParser(conn, plate_id)
    file_annotation = parser._check_excel_file()
    assert file_annotation is not None, "Excel file should be found"
    channel_data, well_data = parser._load_data_from_excel(file_annotation)
    assert list(channel_data.keys()) == ["DAPI", "Tub", "EdU"]
    assert list(channel_data.values()) == ["0", "1", "2"]
    assert well_data["Well"] == ["C2", "C5"]
    assert well_data["cell_line"] == ["RPE-1", "RPE-1"]
    assert well_data["condition"] == ["Ctr", "Cdk4"]


def test_load_data_from_excel_failure(base_plate, attach_excel):
    """Test that an error is raised when Excel file has invalid format."""
    # ruff: noqa: F841
    file1 = attach_excel(
        base_plate, {"Sheet1": pd.DataFrame({"A": [1, 2], "B": [3, 4]})}
    )
    plate_id = base_plate.getId()
    conn = base_plate._conn
    parser = MetadataParser(conn, plate_id)
    file_annotation = parser._check_excel_file()
    assert file_annotation is not None, "Excel file should be found"
    with pytest.raises(ExcelParsingError) as exc_info:
        parser._load_data_from_excel(file_annotation)
    assert (
        str(exc_info.value)
        == "Invalid excel file format - expected Sheet1 and Sheet2"
    )


def test_excel_file_check_multiple_files(base_plate, attach_excel):
    """Test that error is raised when multiple Excel files are present"""
    # Attach two Excel files
    attach_excel(base_plate, {"Sheet1": pd.DataFrame({"A": [1, 2]})})
    attach_excel(base_plate, {"Sheet1": pd.DataFrame({"B": [3, 4]})})

    plate_id = base_plate.getId()
    conn = base_plate._conn
    parser = MetadataParser(conn, plate_id)

    with pytest.raises(ExcelParsingError) as exc_info:
        parser._check_excel_file()
    assert str(exc_info.value) == "Multiple Excel files found on plate"


def test_parse_channel_annotations_no_annotations(base_plate):
    """Test that appropriate error is raised when no channel annotations exist."""
    plate_id = base_plate.getId()
    conn = base_plate._conn
    parser = MetadataParser(conn, plate_id)

    with pytest.raises(ChannelAnnotationError) as exc_info:
        parser._parse_channel_annotations()
    assert str(exc_info.value) == "No channel annotations found on plate"


def test_parse_channel_annotations_success(base_plate_with_annotations):
    """Test that channel annotations are correctly parsed."""
    plate = base_plate_with_annotations
    parser = MetadataParser(plate._conn, plate.getId())
    channel_data = parser._parse_channel_annotations()

    # Check that we got all expected channels with correct indices
    expected_channels = {"DAPI": "0", "Tub": "1", "EdU": "2"}
    print(channel_data)
    assert channel_data == expected_channels, (
        f"Expected channel data {expected_channels}, got {channel_data}"
    )


def test_parse_well_annotations_success(base_plate_with_annotations):
    """Test that well annotations are correctly parsed."""
    plate = base_plate_with_annotations
    parser = MetadataParser(plate._conn, plate.getId())
    well_data = parser._parse_well_annotations()

    # Get indices that would sort the wells alphabetically
    sort_indices = sorted(
        range(len(well_data["Well"])), key=lambda k: well_data["Well"][k]
    )

    # Sort all lists in well_data based on Well order
    sorted_well_data = {
        key: [values[i] for i in sort_indices]
        for key, values in well_data.items()
    }

    assert sorted_well_data["Well"] == ["C2", "C5"]
    assert sorted_well_data["cell_line"] == ["RPE-1", "RPE-1"]
    assert sorted_well_data["condition"] == ["Ctr", "Cdk4"]


def test_parse_well_annotations_failure(base_plate):
    """Test that appropriate error is raised when no well annotations exist."""
    for well in base_plate.listChildren():
        for ann in well.listAnnotations():
            base_plate._conn.deleteObject(ann._obj)

    for ann in base_plate.listAnnotations():
        base_plate._conn.deleteObject(ann._obj)
    plate_id = base_plate.getId()
    conn = base_plate._conn
    parser = MetadataParser(conn, plate_id)

    with pytest.raises(WellAnnotationError) as exc_info:
        parser._parse_well_annotations()
    error_message = str(exc_info.value)
    assert error_message.startswith("No well annotations found for well")
    assert any(well_id in error_message for well_id in ["C2", "C5"])


# --------------------Validation Checks--------------------
# Im mocking the parser class here to test the validation methods


class MockParser(MetadataParser):
    """Mock parser class for testing channel data validation."""

    def __init__(self, channel_data=None, well_data=None, plate_wells=None):
        # Skip parent class initialization by not calling super().__init__()
        # This avoids the need for OMERO connection objects
        self.channel_data = channel_data if channel_data is not None else {}
        self.well_data = well_data if well_data is not None else {}

        # Create mock well class
        class MockWell:
            def __init__(self, pos):
                self.pos = pos

            def getWellPos(self):
                return self.pos

        # Create mock plate class
        class MockPlate:
            def __init__(self, wells):
                self.wells = wells

            def listChildren(self):
                return [MockWell(pos) for pos in wells]

        # Initialize mock plate
        wells = plate_wells or []
        self.plate = MockPlate(wells)

    # Override the method to refresh the plate from OMERO
    def _get_plate(self) -> PlateWrapper:
        return self.plate


# --------------------TEST Validate Metadata Structure--------------------


def test_validate_metadata_structure_success():
    """Test that valid metadata structure passes validation."""
    parser = MockParser(
        channel_data={"DAPI": "0", "GFP": "1"},
        well_data={"Well": ["A1", "A2"], "condition": ["ctrl", "treat"]},
    )
    errors = parser._validate_metadata_structure()
    assert not errors, "No errors should be found for valid metadata"


def test_validate_metadata_structure_missing_channel_data():
    """Test that missing channel data raises an error."""
    parser = MockParser(well_data={"Well": ["A1", "A2"]})
    errors = parser._validate_metadata_structure()
    assert len(errors) == 1
    assert errors[0] == "No channel data found"


def test_validate_metadata_structure_missing_well_data():
    """Test that missing well data raises an error."""
    parser = MockParser(channel_data={"DAPI": "0", "GFP": "1"})
    errors = parser._validate_metadata_structure()
    assert len(errors) == 1
    assert errors[0] == "No well data found"


def test_validate_metadata_structure_invalid_channel_keys():
    """Test that non-string channel keys raise an error."""
    parser = MockParser(
        channel_data={1: "0", "GFP": "1"}, well_data={"Well": ["A1"]}
    )
    errors = parser._validate_metadata_structure()
    assert len(errors) == 1
    assert errors[0] == "Channel data must be a dictionary with string keys"


def test_validate_metadata_structure_invalid_channel_values():
    """Test that non-string channel values raise an error."""
    parser = MockParser(
        channel_data={"DAPI": 0, "GFP": "1"},  # 0 is an int, not a string
        well_data={"Well": ["A1"]},
    )
    errors = parser._validate_metadata_structure()
    assert len(errors) == 1
    assert errors[0] == "Channel data must be a dictionary with string values"


def test_validate_metadata_structure_multiple_errors():
    """Test that multiple validation errors are collected."""
    parser = MockParser(
        channel_data={1: 0},  # Invalid key and value
        well_data={"Well": "A1"},  # Not a list
    )
    errors = parser._validate_metadata_structure()
    assert len(errors) == 2  # Invalid key and invalid value
    assert "Channel data must be a dictionary with string keys" in errors
    assert "Channel data must be a dictionary with string values" in errors


# --------------------TEST Validate Channel Data--------------------


def test_validate_channel_data_with_dapi():
    """Test that DAPI channel passes validation and remains unchanged."""
    parser = MockParser({"DAPI": "0", "GFP": "1"})
    errors = parser._validate_channel_data()
    assert not errors
    assert parser.channel_data == {"DAPI": "0", "GFP": "1"}


def test_validate_channel_data_normalize_hoechst():
    """Test that Hoechst is normalized to DAPI."""
    parser = MockParser({"Hoechst": 0, "GFP": 1})
    parser._validate_channel_data()
    assert "DAPI" in parser.channel_data
    assert parser.channel_data["DAPI"] == 0
    assert "Hoechst" not in parser.channel_data
    assert parser.channel_data["GFP"] == 1


def test_validate_channel_data_normalize_dna():
    """Test that DNA is normalized to DAPI."""
    parser = MockParser({"DNA": 0, "GFP": 1})
    parser._validate_channel_data()
    assert "DAPI" in parser.channel_data
    assert parser.channel_data["DAPI"] == 0
    assert "DNA" not in parser.channel_data
    assert parser.channel_data["GFP"] == 1


def test_validate_channel_data_normalize_rfp():
    """Test that RFP is normalized to DAPI."""
    parser = MockParser({"RFP": 0, "GFP": 1})
    parser._validate_channel_data()
    assert "DAPI" in parser.channel_data
    assert parser.channel_data["DAPI"] == 0
    assert "RFP" not in parser.channel_data
    assert parser.channel_data["GFP"] == 1


def test_validate_channel_data_case_insensitive():
    """Test that nuclei channel detection is case insensitive."""
    parser = MockParser({"dapi": 0, "GFP": 1})
    parser._validate_channel_data()
    assert "DAPI" in parser.channel_data
    assert parser.channel_data["DAPI"] == 0
    assert parser.channel_data["GFP"] == 1


def test_validate_channel_data_no_nuclei_channel():
    """Test that validation fails when no nuclei channel is present."""
    parser = MockParser({"GFP": "0", "YFP": "1"})
    errors = parser._validate_channel_data()
    assert len(errors) == 1
    assert (
        "At least one nuclei channel (DAPI/Hoechst/DNA/RFP) is required"
        in errors[0]
    )


# --------------------TEST Validate Well Data--------------------


def test_validate_well_data_success():
    """Test that valid well data passes validation."""
    parser = MockParser(
        well_data={
            "Well": ["A1", "A2"],
            "cell_line": ["RPE1", "RPE1"],
            "condition": ["ctrl", "treat"],
        },
        plate_wells=["A1", "A2"],
    )
    errors = parser._validate_well_data()
    assert not errors


def test_validate_well_data_missing_required_key():
    """Test that missing required key raises an error."""
    parser = MockParser(
        well_data={
            "Well": ["A1", "A2"],
            "condition": ["ctrl", "treat"],  # Missing cell_line
        },
        plate_wells=["A1", "A2"],
    )
    errors = parser._validate_well_data()
    assert len(errors) == 1
    assert "Missing required keys in well data: cell_line" in errors[0]


def test_validate_well_data_non_list_values():
    """Test that non-list values raise an error."""
    parser = MockParser(
        well_data={
            "Well": ["A1", "A2"],
            "cell_line": "RPE1",  # Should be a list
            "condition": ["ctrl", "treat"],
        },
        plate_wells=["A1", "A2"],
    )
    errors = parser._validate_well_data()
    assert (
        len(errors) == 2
    )  # Non-list value error and well position validation error
    assert any(
        "Values must be lists for all keys" in error for error in errors
    )
    assert any("cell_line" in error for error in errors)


def test_validate_well_data_inconsistent_lengths():
    """Test that lists of different lengths raise an error."""
    parser = MockParser(
        well_data={
            "Well": ["A1", "A2"],
            "cell_line": ["RPE1", "RPE1", "RPE1"],  # One extra value
            "condition": ["ctrl", "treat"],
        },
        plate_wells=["A1", "A2"],
    )
    errors = parser._validate_well_data()
    assert len(errors) == 1
    assert "All well data lists must have the same length" in errors[0]
    assert "Well: 2" in errors[0]
    assert "cell_line: 3" in errors[0]


def test_validate_well_data_wrong_well_order():
    """Test that wrong well order is allowed."""
    parser = MockParser(
        well_data={
            "Well": ["A2", "A1"],  # Wrong order
            "cell_line": ["RPE1", "RPE1"],
            "condition": ["ctrl", "treat"],
        },
        plate_wells=["A1", "A2"],
    )
    parser._validate_well_data()
    assert parser.well_conditions("A1")["condition"] == "treat"
    assert parser.well_conditions("A2")["condition"] == "ctrl"


def test_validate_well_data_missing_well():
    """Test that a missing well raises an error."""
    parser = MockParser(
        well_data={
            "Well": ["A1", "A1"],  # Duplicate well
            "cell_line": ["RPE1", "RPE1"],
            "condition": ["ctrl", "treat"],
        },
        plate_wells=["A1", "A2"],
    )
    errors = parser._validate_well_data()
    assert len(errors) == 1
    assert any("Missing wells in metadata" in error for error in errors)


def test_validate_well_data_extra_well():
    """Test that an extra well raises an error."""
    parser = MockParser(
        well_data={
            "Well": ["A1", "A2", "A3"],  # Extra well
            "cell_line": ["RPE1", "RPE1", "TS2"],
            "condition": ["ctrl", "treat", "other"],
        },
        plate_wells=["A1", "A2"],
    )
    errors = parser._validate_well_data()
    assert len(errors) == 1
    assert any("Extra wells in metadata" in error for error in errors)


def test_validate_well_data_multiple_errors():
    """Test that multiple well data errors are collected."""
    parser = MockParser(
        well_data={
            "Well": ["A2", "A3"],  # Wrong wells
            "cell_line": "RPE1",  # Not a list
            "condition": ["ctrl", "treat"],
        },
        plate_wells=["A1", "A2"],
    )
    errors = parser._validate_well_data()
    assert (
        len(errors) == 4
    )  # Non-list value error, well order error, and well position validation error
    assert any(
        "Values must be lists for all keys" in error for error in errors
    )
    assert any("Missing wells in metadata" in error for error in errors)
    assert any("Extra wells in metadata" in error for error in errors)
    assert any("cell_line" in error for error in errors)
