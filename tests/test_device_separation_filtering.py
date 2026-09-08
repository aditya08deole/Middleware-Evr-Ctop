"""
Comprehensive tests for:
1. Device separation (no data confusion between devices)
2. Filtering algorithms (median, average with correct data points)
3. Duplicate detection (only NEW data is sent)
4. Temperature compensation (EvaraTank specific)
"""

import pytest
from datetime import datetime
from services import PreprocessService, ThingSpeakService
from models import db, Device, ProcessedData


class TestDeviceSeparation:
    """Test that devices are processed independently with no confusion"""

    def test_separate_devices_separate_processing(self, app, db_session):
        """Test that device A and device B are processed independently"""
        with app.app_context():
            # Create two devices
            device_a = Device(
                id=1,
                name='Device A',
                device_type='EvaraTank',
                channel_id='111111',
                api_key='key_a',
                ctop_url_1='https://ctop-a.com/api',
                auth_token='token_a',
                tank_height=100,
                distance_field='field1',
                temperature_field='field2',
                filtering_method='none',
                is_active=True
            )
            device_b = Device(
                id=2,
                name='Device B',
                device_type='EvaraFlow',
                channel_id='222222',
                api_key='key_b',
                ctop_url_1='https://ctop-b.com/api',
                auth_token='token_b',
                meter_reading_field='field1',
                flow_rate_field='field2',
                filtering_method='none',
                is_active=True
            )
            db.session.add(device_a)
            db.session.add(device_b)
            db.session.commit()

            # Prepare service
            service = PreprocessService()

            # Data for Device A (EvaraTank - has distance + temperature)
            raw_data_a = {
                'channel': {'id': 111111},
                'feeds': [{
                    'entry_id': '1',
                    'created_at': '2026-05-12T10:00:00Z',
                    'field1': '45.5',  # distance
                    'field2': '28.0'   # temperature
                }]
            }

            # Data for Device B (EvaraFlow - has meter_reading + flow_rate)
            raw_data_b = {
                'channel': {'id': 222222},
                'feeds': [{
                    'entry_id': '1',
                    'created_at': '2026-05-12T10:00:00Z',
                    'field1': '1200.5',   # meter reading
                    'field2': '0.85'      # flow rate
                }]
            }

            # Process Device A
            success_a, processed_a, error_a = service.preprocess_data(1, raw_data_a)
            assert success_a is True
            assert len(processed_a) > 0
            assert 'Level' in processed_a[0]  # EvaraTank specific field
            assert processed_a[0]['Level'] == pytest.approx(54.5, rel=0.1)

            # Process Device B
            success_b, processed_b, error_b = service.preprocess_data(2, raw_data_b)
            assert success_b is True
            assert len(processed_b) > 0
            assert 'MeterReading' in processed_b[0]  # EvaraFlow specific field
            assert processed_b[0]['MeterReading'] == pytest.approx(1200.5)

            # Verify devices processed independently
            # (no data from A in B's results and vice versa)
            assert 'FlowRate' not in processed_a[0]  # Device B's field
            assert 'Level' not in processed_b[0]  # Device A's field

    def test_device_fields_not_mixed(self, app, db_session):
        """Test that fields from different device types don't get mixed"""
        with app.app_context():
            device_tank = Device(
                id=1,
                name='Tank',
                device_type='EvaraTank',
                channel_id='111',
                api_key='key',
                ctop_url_1='https://ctop.com/api',
                auth_token='token',
                tank_height=100,
                distance_field='field1',
                temperature_field='field2',
                is_active=True
            )
            device_valve = Device(
                id=2,
                name='Valve',
                device_type='EvaraValve',
                channel_id='222',
                api_key='key',
                ctop_url_1='https://ctop.com/api',
                auth_token='token',
                flow_rate_field='field1',
                liters_field='field2',
                is_active=True
            )
            db.session.add(device_tank)
            db.session.add(device_valve)
            db.session.commit()

            service = PreprocessService()

            # Same entry_id but different devices
            raw_data_tank = {
                'channel': {'id': 111},
                'feeds': [{
                    'entry_id': '999',  # Same ID
                    'created_at': '2026-05-12T10:00:00Z',
                    'field1': '45.5',
                    'field2': '28.0'
                }]
            }

            raw_data_valve = {
                'channel': {'id': 222},
                'feeds': [{
                    'entry_id': '999',  # Same ID
                    'created_at': '2026-05-12T10:00:00Z',
                    'field1': '0.85',
                    'field2': '850'
                }]
            }

            success_tank, processed_tank, _ = service.preprocess_data(1, raw_data_tank)
            success_valve, processed_valve, _ = service.preprocess_data(2, raw_data_valve)

            assert success_tank is True
            assert success_valve is True

            # Verify correct field interpretation per device
            # Level reflects temperature-compensated distance (28°C, not the
            # reference 25°C), so it's 100 - compensated(45.5, 28) ≈ 54.26,
            # not a plain 100 - 45.5 = 54.5 subtraction.
            assert processed_tank[0]['Level'] == pytest.approx(54.26, abs=0.01)
            assert processed_valve[0]['FlowRate'] == pytest.approx(0.85)
            assert processed_valve[0]['Liters'] == pytest.approx(850)


class TestFilteringAlgorithms:
    """Test filtering algorithms (median, average) work correctly"""

    def test_median_filter_basic(self, app, db_session):
        """Test median filter with basic data points"""
        with app.app_context():
            service = PreprocessService()

            # Test data with 5 points (median should pick middle)
            values = [10.0, 20.0, 30.0, 40.0, 50.0]
            window = 5

            filtered = service._apply_median_filter(values, window)

            # _apply_median_filter uses a centered window (±window//2 around
            # each index, clamped to the array bounds), not a causal/trailing
            # one — this matches the already-validated behavior in
            # test_preprocess_partial_fields.py::test_filters_with_none.
            assert len(filtered) == 5
            # First value: window is [10, 20, 30] (clamped at the left edge) -> median 20
            assert filtered[0] == 20.0
            # Second value: window is [10, 20, 30, 40] -> median (20+30)/2 = 25
            assert filtered[1] == 25.0
            # Third value: window is the full [10, 20, 30, 40, 50] -> median 30
            assert filtered[2] == 30.0

    def test_median_filter_removes_spikes(self, app, db_session):
        """Test that median filter removes spikes correctly"""
        with app.app_context():
            service = PreprocessService()

            # Data with spike
            values = [45.0, 45.1, 99.9, 45.2, 45.0]  # 99.9 is spike
            window = 5

            filtered = service._apply_median_filter(values, window)

            # At spike position, median should reduce the spike
            # [45.0, 45.1, 45.2] = median 45.1 (spike removed)
            assert filtered[2] < 99.9  # Spike should be reduced

    def test_average_filter_basic(self, app, db_session):
        """Test average filter with basic data points"""
        with app.app_context():
            service = PreprocessService()

            values = [10.0, 20.0, 30.0, 40.0, 50.0]
            window = 5

            filtered = service._apply_average_filter(values, window)

            # Same centered-window semantics as the median filter above.
            assert len(filtered) == 5
            # First value: window is [10, 20, 30] -> avg 20
            assert filtered[0] == 20.0
            # Third value: window is the full [10, 20, 30, 40, 50] -> avg 30
            assert filtered[2] == 30.0

    def test_window_size_impact(self, app, db_session):
        """Test that window size affects filtering output"""
        with app.app_context():
            service = PreprocessService()

            values = [10.0, 50.0, 10.0, 50.0, 10.0]

            # Small window (1) - no filtering
            filtered_w1 = service._apply_median_filter(values, 1)
            assert filtered_w1 == values

            # Large window (5) - more smoothing
            filtered_w5 = service._apply_median_filter(values, 5)
            # Full-array window at index 2: sorted([10,50,10,50,10]) has
            # median 10 (three 10s outnumber two 50s) — still meaningfully
            # different from the unfiltered value only where a spike sits
            # alone; the point of this test is filtered_w1 != filtered_w5.
            assert filtered_w5[2] == 10.0
            assert filtered_w1 != filtered_w5

    def test_filtering_maintains_length(self, app, db_session):
        """Test that filtering maintains list length"""
        with app.app_context():
            service = PreprocessService()

            values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

            median = service._apply_median_filter(values, 5)
            average = service._apply_average_filter(values, 3)

            assert len(median) == len(values)
            assert len(average) == len(values)


class TestDuplicateDetection:
    """Test that duplicate entries are detected and skipped"""

    def test_duplicate_entry_filtered(self, app, db_session):
        """Test that already-processed entries are filtered out"""
        with app.app_context():
            # _filter_duplicate_entries tracks "already seen" entry_ids
            # in-memory on the ThingSpeakService instance itself
            # (self.device_last_entry_id), not by querying the ProcessedData
            # table — see the comment in thingspeak_service.py:fetch_data
            # explaining why the DB-backed approach was replaced (a failed
            # CTOP send would otherwise get marked "seen" and never retried).
            # Simulate "already processed up to 12345" the way the real code
            # actually tracks it.
            service = ThingSpeakService()
            service.device_last_entry_id['1'] = '12345'

            # Try to fetch same entry again
            raw_data = {
                'channel': {'id': 111},
                'feeds': [{
                    'entry_id': '12345',  # Same as before
                    'created_at': '2026-05-12T10:00:00Z',
                    'field1': '45.5',
                    'field2': '28.0'
                }]
            }

            # Filter should remove duplicate
            filtered = service._filter_duplicate_entries('1', raw_data)

            assert len(filtered['feeds']) == 0  # Duplicate removed

    def test_new_entry_not_filtered(self, app, db_session):
        """Test that new entries are NOT filtered"""
        with app.app_context():
            device = Device(
                id=1,
                name='Test Device',
                device_type='EvaraTank',
                channel_id='111',
                api_key='key',
                ctop_url_1='https://ctop.com/api',
                auth_token='token',
                tank_height=100,
                distance_field='field1',
                temperature_field='field2',
                is_active=True
            )
            db.session.add(device)
            db.session.commit()

            service = ThingSpeakService()

            # Fresh entry (never processed)
            raw_data = {
                'channel': {'id': 111},
                'feeds': [{
                    'entry_id': '99999',  # New entry
                    'created_at': '2026-05-12T10:00:00Z',
                    'field1': '45.5',
                    'field2': '28.0'
                }]
            }

            filtered = service._filter_duplicate_entries('1', raw_data)

            assert len(filtered['feeds']) == 1  # New entry kept
            assert filtered['feeds'][0]['entry_id'] == '99999'


class TestTemperatureCompensation:
    """Test temperature compensation for EvaraTank"""

    def test_temperature_compensation_formula(self, app, db_session):
        """Test that temperature compensation follows correct formula"""
        with app.app_context():
            device = Device(
                id=1,
                name='Tank',
                device_type='EvaraTank',
                channel_id='111',
                api_key='key',
                ctop_url_1='https://ctop.com/api',
                auth_token='token',
                tank_height=100,
                distance_field='field1',
                temperature_field='field2',
                is_active=True
            )
            db.session.add(device)
            db.session.commit()

            service = PreprocessService()

            # Reference: at 25°C, no compensation needed
            comp_at_25 = service._apply_temperature_compensation(50.0, 25.0)
            assert comp_at_25 == pytest.approx(50.0, rel=0.01)

            # At higher temperature, compensated distance should change
            comp_at_30 = service._apply_temperature_compensation(50.0, 30.0)
            # Speed increases, sensor reads shorter distance for same real distance
            assert comp_at_30 != comp_at_25

    def test_temperature_compensation_in_preprocessing(self, app, db_session):
        """Test that temperature compensation is applied during preprocessing"""
        with app.app_context():
            device = Device(
                id=1,
                name='Tank',
                device_type='EvaraTank',
                channel_id='111',
                api_key='key',
                ctop_url_1='https://ctop.com/api',
                auth_token='token',
                tank_height=100,
                distance_field='field1',
                temperature_field='field2',
                filtering_method='none',
                is_active=True
            )
            db.session.add(device)
            db.session.commit()

            service = PreprocessService()

            raw_data = {
                'channel': {'id': 111},
                'feeds': [{
                    'entry_id': '1',
                    'created_at': '2026-05-12T10:00:00Z',
                    'field1': '50.0',  # distance at 30°C
                    'field2': '30.0'   # temperature
                }]
            }

            success, processed, _ = service.preprocess_data(1, raw_data)

            assert success is True
            assert len(processed) > 0
            # Level should be calculated with compensation
            # Level = tank_height - compensated_distance
            # With temp 30°C, distance is compensated
            assert processed[0]['CompensatedDistance'] != 50.0  # Compensation applied


class TestTransformationToCtopFormat:
    """Test transformation to CTOP format"""

    def test_only_sensor_fields_sent(self, app, db_session):
        """Test that only sensor fields (no metadata) are sent to CTOP"""
        with app.app_context():
            device = Device(
                id=1,
                name='Tank',
                device_type='EvaraTank',
                channel_id='111',
                api_key='key',
                ctop_url_1='https://ctop.com/api',
                auth_token='token',
                tank_height=100,
                distance_field='field1',
                temperature_field='field2',
                is_active=True
            )
            db.session.add(device)
            db.session.commit()

            service = PreprocessService()

            processed_data = [{
                'Level': 54.5,
                'Temperature': 28.0,
                'RawDistance': 50.0,
                'CompensatedDistance': 49.5,
                'TankHeight': 100,
                'Latitude': 17.385,
                'Longitude': 78.486,
                'LCT': '2026-05-12T10:00:00Z',
                'entry_id': '1'
            }]

            transformed = service.transform_to_ctop_format(1, processed_data)

            assert len(transformed) > 0
            payload = transformed[0]

            # Only sensor fields should be present
            assert 'water_level' in payload
            assert 'temperature' in payload

            # Metadata should NOT be present
            assert 'latitude' not in payload
            assert 'longitude' not in payload
            assert 'LCT' not in payload
            assert 'entry_id' not in payload


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
