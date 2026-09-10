from services.preprocess_service import PreprocessService


class MockDevice:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_evaraflow_partial_fields():
    service = PreprocessService()

    # 1. Device with both fields configured
    device = MockDevice(
        device_type="EvaraFlow",
        meter_reading_field="field3",
        flow_rate_field="field2",
        filtering_method="none",
        filter_window=5,
        latitude=12.97,
        longitude=77.59,
    )

    # Feed 1: Both fields present
    # Feed 2: Only MeterReading present
    # Feed 3: Only FlowRate present
    # Feed 4: Both fields None
    feeds = [
        {
            "entry_id": "1",
            "created_at": "2026-07-11T12:00:00Z",
            "field3": "100.5",
            "field2": "1.2",
        },
        {
            "entry_id": "2",
            "created_at": "2026-07-11T12:01:00Z",
            "field3": "101.2",
            "field2": None,
        },
        {
            "entry_id": "3",
            "created_at": "2026-07-11T12:02:00Z",
            "field3": None,
            "field2": "1.5",
        },
        {
            "entry_id": "4",
            "created_at": "2026-07-11T12:03:00Z",
            "field3": None,
            "field2": None,
        },
    ]

    processed = service._preprocess_evaraflow("test-device-flow", device, feeds)

    assert len(processed) == 3

    # Feed 1 assertion
    assert processed[0]["entry_id"] == "1"
    assert processed[0]["MeterReading"] == 100.5
    assert processed[0]["FlowRate"] == 1.2

    # Feed 2 assertion (only MeterReading, FlowRate defaults to 0.0)
    assert processed[1]["entry_id"] == "2"
    assert processed[1]["MeterReading"] == 101.2
    assert processed[1]["FlowRate"] == 0.0

    # Feed 3 assertion (only FlowRate)
    assert processed[2]["entry_id"] == "3"
    assert "MeterReading" not in processed[2]
    assert processed[2]["FlowRate"] == 1.5


def test_evaratank_partial_fields():
    service = PreprocessService()

    device = MockDevice(
        device_type="EvaraTank",
        tank_height=200,
        distance_field="field1",
        temperature_field="field2",
        filtering_method="none",
        filter_window=5,
        latitude=None,
        longitude=None,
    )

    # Feed 1: Both present
    # Feed 2: Distance only (no temperature) -> raw level calculation
    # Feed 3: Temperature only
    feeds = [
        {
            "entry_id": "1",
            "created_at": "2026-07-11T12:00:00Z",
            "field1": "50",
            "field2": "25",
        },
        {
            "entry_id": "2",
            "created_at": "2026-07-11T12:01:00Z",
            "field1": "60",
            "field2": None,
        },
        {
            "entry_id": "3",
            "created_at": "2026-07-11T12:02:00Z",
            "field1": None,
            "field2": "28",
        },
    ]

    processed = service._preprocess_evaratank("test-device-tank", device, feeds)

    assert len(processed) == 3

    # Feed 1 (temp compensation applied: compensated distance ~ 48.9, level ~ 151.1)
    assert processed[0]["entry_id"] == "1"
    assert "Level" in processed[0]
    assert processed[0]["Temperature"] == 25.0

    # Feed 2 (no temp compensation, raw level: 200 - 60 = 140)
    assert processed[1]["entry_id"] == "2"
    assert processed[1]["Level"] == 140.0
    assert "Temperature" not in processed[1]

    # Feed 3 (no level, only temperature)
    assert processed[2]["entry_id"] == "3"
    assert "Level" not in processed[2]
    assert processed[2]["Temperature"] == 28.0


def test_filters_with_none():
    service = PreprocessService()

    values = [1.0, None, 2.0, 3.0, None, 4.0]

    # Median filter should handle None
    median_filtered = service._apply_median_filter(values, 3)
    assert len(median_filtered) == len(values)
    assert median_filtered[0] == 1.0  # window [1.0, None] -> non-none [1.0] -> 1.0
    assert median_filtered[1] is None  # original is None
    assert (
        median_filtered[2] == 2.5
    )  # window [None, 2.0, 3.0] -> non-none [2.0, 3.0] -> median 2.5

    # Average filter should handle None
    avg_filtered = service._apply_average_filter(values, 3)
    assert len(avg_filtered) == len(values)
    assert avg_filtered[0] == 1.0
    assert avg_filtered[1] is None
    assert (
        avg_filtered[2] == 2.5
    )  # window [None, 2.0, 3.0] -> non-none [2.0, 3.0] -> average 2.5
