import json
import threading
from collections import deque
from datetime import datetime
from models import db, Device, ProcessedData


class PreprocessService:
    """Service for preprocessing and transforming ThingSpeak data"""

    def __init__(self):
        # Default field mappings for unknown device types (field1-8 → fieldN passthrough)
        self.field_mappings = {f"field{i}": f"field{i}" for i in range(1, 9)}

        # Per-(device_id, field_key) rolling history of raw values, used by
        # _apply_streaming_filter for median/average smoothing that persists
        # across calls. A single preprocess_data() call almost always sees
        # just one new reading (ThingSpeak fetches results=1; EMQX delivers
        # ~1 buffered message per instant-trigger), so filtering computed
        # only from that call's own feeds always collapsed the configured
        # window down to 1 — i.e. the configured filter had no smoothing
        # effect at all in the common case. This service instance is a
        # long-lived singleton (one per process, reused every scheduler
        # tick), so keeping history here lets the window actually span
        # consecutive polls.
        self._filter_history = {}
        self._filter_history_lock = threading.Lock()
        # Bound history length regardless of configured filter_window so a
        # large/misconfigured window can't grow memory unbounded per device.
        self._MAX_FILTER_HISTORY = 50

    def preprocess_data(self, device_id, raw_data, device_data=None):
        """
        Preprocess raw ThingSpeak data

        Args:
            device_id: ID of the device
            raw_data: Raw JSON data from ThingSpeak
            device_data: Optional device dictionary (for Firestore mode)

        Returns:
            tuple: (success: bool, processed_data: list, error: str or None)
        """
        if device_data:
            from models.dataclass_models import DeviceModel

            device = DeviceModel.from_dict(device_data)
        else:
            device = db.session.get(Device, device_id)

        if not device:
            return False, None, "Device not found"

        if not raw_data or "feeds" not in raw_data:
            return False, None, "Invalid data format: missing 'feeds'"

        processed_entries = []
        skipped_count = 0

        try:
            # Handle device type specific preprocessing
            if device.device_type == "EvaraTank":
                processed_entries = self._preprocess_evaratank(
                    device_id, device, raw_data["feeds"]
                )
            elif device.device_type == "EvaraFlow":
                processed_entries = self._preprocess_evaraflow(
                    device_id, device, raw_data["feeds"]
                )
            elif device.device_type == "EvaraValve":
                processed_entries = self._preprocess_evaravalve(
                    device_id, device, raw_data["feeds"]
                )
            elif device.device_type == "EvaraDeep":
                processed_entries = self._preprocess_evaradeep(
                    device_id, device, raw_data["feeds"]
                )
            elif device.device_type == "EvaraTDS":
                processed_entries = self._preprocess_evaratds(
                    device_id, device, raw_data["feeds"]
                )
            else:
                # Default passthrough preprocessing for unrecognized device types
                logger = self._get_logger()
                logger.warning(
                    f"Unknown device_type '{device.device_type}' for device {getattr(device, 'id', device_id)} — using generic field passthrough."
                )
                for feed in raw_data["feeds"]:
                    processed = self._process_feed(feed, device)

                    if processed:
                        processed_entries.append(processed)
                    else:
                        skipped_count += 1

            # Log preprocessing
            self._log_preprocess(device, len(processed_entries), skipped_count, None)

            return True, processed_entries, None

        except Exception as e:
            error = f"Preprocessing error: {str(e)}"
            self._log_preprocess(device, 0, 0, error)
            return False, None, error

    def _process_feed(self, feed, device):
        """
        Process a single feed entry

        Args:
            feed: Single feed entry from ThingSpeak
            device: Device object

        Returns:
            dict: Processed entry or None if invalid
        """
        processed = {}

        # Skip if no entry_id
        if not feed.get("entry_id"):
            return None

        # Map fields using field mappings
        for field, mapped_name in self.field_mappings.items():
            value = feed.get(field)
            if value is not None:
                # Convert to appropriate type
                processed[mapped_name] = self._convert_value(value)

        # Add timestamp
        created_at = feed.get("created_at")
        if created_at:
            try:
                processed["LCT"] = self._parse_timestamp(created_at)
            except (ValueError, TypeError):
                # Log the error but continue with raw timestamp
                processed["LCT"] = created_at

        # Add device metadata (location only)
        if device.latitude is not None:
            processed["Latitude"] = device.latitude

        if device.longitude is not None:
            processed["Longitude"] = device.longitude
        # Keep original entry_id for reference
        processed["entry_id"] = feed.get("entry_id")

        # Skip if no meaningful data
        if len(processed) < 2:
            return None

        return processed

    def _preprocess_evaratank(self, device_id, device, feeds):
        """
        Preprocess EvaraTank specific data with temperature compensation and filtering

        Args:
            device: Device object with EvaraTank configuration
            feeds: List of feed entries from ThingSpeak

        Returns:
            list: Processed entries with level and temperature
        """
        processed_entries = []

        # Validate EvaraTank configuration
        if (
            not device.tank_height
            or not device.distance_field
            or not device.temperature_field
        ):
            return processed_entries

        # Extract distance and temperature values from all feeds
        distance_values = []
        temp_values = []
        feed_entries = []

        for feed in feeds:
            if not feed.get("entry_id"):
                continue

            distance = self._safe_numeric(feed.get(device.distance_field))
            temperature = self._safe_numeric(feed.get(device.temperature_field))

            # Keep genuinely missing fields as None (not 0) — the filter
            # functions below already handle None correctly (skip it from
            # window math, propagate it through when nothing else in the
            # window is present), and downstream code decides per-field
            # whether to include Level/Temperature based on `is not None`.
            # Defaulting to 0 here previously made a missing temperature
            # look "present at 0°C" and triggered bogus compensation.
            distance_values.append(distance)
            temp_values.append(temperature)
            feed_entries.append(feed)

        if not feed_entries:
            return processed_entries

        # Apply filtering if configured. Uses a trailing window carried
        # across calls (see _apply_streaming_filter) instead of a window
        # confined to this call's own feeds, which would collapse to a
        # no-op whenever only one new reading arrives (the common case).
        if device.filtering_method in ("median", "average"):
            window = device.filter_window or 5
            filtered_distances = self._apply_streaming_filter(
                device_id, "distance", distance_values, window, device.filtering_method
            )
            filtered_temps = self._apply_streaming_filter(
                device_id, "temperature", temp_values, window, device.filtering_method
            )
        else:
            filtered_distances = distance_values
            filtered_temps = temp_values

        # Process each feed with filtered values
        for i, feed in enumerate(feed_entries):
            if i >= len(filtered_distances) or i >= len(filtered_temps):
                continue

            dist_val = filtered_distances[i]
            temp_val = filtered_temps[i]

            # Nothing usable reported this tick — don't emit an empty payload
            if dist_val is None and temp_val is None:
                continue

            processed = {
                "LCT": (
                    self._parse_timestamp(feed.get("created_at"))
                    if feed.get("created_at")
                    else None
                ),
                "entry_id": feed.get("entry_id"),
            }

            # Level computation requires distance
            if dist_val is not None:
                if temp_val is not None:
                    # Temp compensated distance
                    compensated_distance = self._apply_temperature_compensation(
                        dist_val, temp_val
                    )
                    level = device.tank_height - compensated_distance
                    processed["Level"] = round(level, 2)
                    processed["CompensatedDistance"] = round(compensated_distance, 2)
                else:
                    # Non compensated distance
                    level = device.tank_height - dist_val
                    processed["Level"] = round(level, 2)
                    processed["CompensatedDistance"] = round(dist_val, 2)

                processed["RawDistance"] = round(dist_val, 2)
                processed["TankHeight"] = device.tank_height

            if temp_val is not None:
                processed["Temperature"] = round(temp_val, 2)

            # Add location if available
            if device.latitude is not None:
                processed["Latitude"] = device.latitude
            if device.longitude is not None:
                processed["Longitude"] = device.longitude

            processed_entries.append(processed)

        return processed_entries

    def _preprocess_evaraflow(self, device_id, device, feeds):
        """
        Preprocess EvaraFlow specific data with meter reading and flow rate

        Args:
            device: Device object with EvaraFlow configuration
            feeds: List of feed entries from ThingSpeak

        Returns:
            list: Processed entries with meter reading and flow rate
        """
        processed_entries = []

        # Validate EvaraFlow configuration
        if not device.meter_reading_field or not device.flow_rate_field:
            return processed_entries

        # Extract meter reading and flow rate values from all feeds
        meter_values = []
        flow_rate_values = []
        feed_entries = []

        for feed in feeds:
            if not feed.get("entry_id"):
                continue

            meter_reading = self._safe_numeric(feed.get(device.meter_reading_field))
            flow_rate = self._safe_numeric(feed.get(device.flow_rate_field))

            # Keep missing fields as None — see _preprocess_evaratank for why.
            meter_values.append(meter_reading)
            flow_rate_values.append(flow_rate)
            feed_entries.append(feed)

        if not feed_entries:
            return processed_entries

        # Apply filtering if configured (trailing window across calls — see
        # _apply_streaming_filter).
        if device.filtering_method in ("median", "average"):
            window = device.filter_window or 5
            filtered_meters = self._apply_streaming_filter(
                device_id,
                "meter_reading",
                meter_values,
                window,
                device.filtering_method,
            )
            filtered_flows = self._apply_streaming_filter(
                device_id,
                "flow_rate",
                flow_rate_values,
                window,
                device.filtering_method,
            )
        else:
            filtered_meters = meter_values
            filtered_flows = flow_rate_values

        # Process each feed with filtered values (NO subtraction like EvaraTank)
        for i, feed in enumerate(feed_entries):
            if i >= len(filtered_meters) or i >= len(filtered_flows):
                continue

            meter_val = filtered_meters[i]
            flow_val = filtered_flows[i]

            # Nothing usable reported this tick — don't emit a fabricated
            # zero-consumption reading (was: {'MeterReading': None-omitted,
            # 'FlowRate': 0.0} sent to CTOP as if it were a real reading).
            if meter_val is None and flow_val is None:
                continue

            processed = {
                "LCT": (
                    self._parse_timestamp(feed.get("created_at"))
                    if feed.get("created_at")
                    else None
                ),
                "entry_id": feed.get("entry_id"),
            }

            if meter_val is not None:
                processed["MeterReading"] = round(meter_val, 2)

            # Send 0 for flow rate if not received (but meter WAS received —
            # the both-missing case is already skipped above)
            if flow_val is not None:
                processed["FlowRate"] = round(flow_val, 2)
            else:
                processed["FlowRate"] = 0.0

            # Add location if available
            if device.latitude is not None:
                processed["Latitude"] = device.latitude
            if device.longitude is not None:
                processed["Longitude"] = device.longitude

            processed_entries.append(processed)

        return processed_entries

    def _preprocess_evaravalve(self, device_id, device, feeds):
        """
        Preprocess EvaraValve specific data with flow rate and liters

        Args:
            device: Device object with EvaraValve configuration
            feeds: List of feed entries from ThingSpeak

        Returns:
            list: Processed entries with flow rate and liters
        """
        processed_entries = []

        # Validate EvaraValve configuration
        if not device.flow_rate_field or not device.liters_field:
            return processed_entries

        # Extract flow rate and liters values from all feeds
        flow_rate_values = []
        liters_values = []
        feed_entries = []

        for feed in feeds:
            if not feed.get("entry_id"):
                continue

            flow_rate = self._safe_numeric(feed.get(device.flow_rate_field))
            liters = self._safe_numeric(feed.get(device.liters_field))

            # Keep missing fields as None — see _preprocess_evaratank for why.
            flow_rate_values.append(flow_rate)
            liters_values.append(liters)
            feed_entries.append(feed)

        if not feed_entries:
            return processed_entries

        # Apply filtering if configured (trailing window across calls — see
        # _apply_streaming_filter).
        if device.filtering_method in ("median", "average"):
            window = device.filter_window or 5
            filtered_flow_rates = self._apply_streaming_filter(
                device_id,
                "flow_rate",
                flow_rate_values,
                window,
                device.filtering_method,
            )
            filtered_liters = self._apply_streaming_filter(
                device_id, "liters", liters_values, window, device.filtering_method
            )
        else:
            filtered_flow_rates = flow_rate_values
            filtered_liters = liters_values

        # Process each feed with filtered values
        for i, feed in enumerate(feed_entries):
            if i >= len(filtered_flow_rates) or i >= len(filtered_liters):
                continue

            flow_val = filtered_flow_rates[i]
            liters_val = filtered_liters[i]

            # Nothing usable reported this tick — don't emit a fabricated reading
            if flow_val is None and liters_val is None:
                continue

            processed = {
                "LCT": (
                    self._parse_timestamp(feed.get("created_at"))
                    if feed.get("created_at")
                    else None
                ),
                "entry_id": feed.get("entry_id"),
            }

            # Send 0 for flow rate if not received (liters WAS received —
            # the both-missing case is already skipped above)
            if flow_val is not None:
                processed["FlowRate"] = round(flow_val, 2)
            else:
                processed["FlowRate"] = 0.0

            if liters_val is not None:
                processed["Liters"] = round(liters_val, 2)

            # Add location if available
            if device.latitude is not None:
                processed["Latitude"] = device.latitude
            if device.longitude is not None:
                processed["Longitude"] = device.longitude

            processed_entries.append(processed)

        return processed_entries

    def _preprocess_evaradeep(self, device_id, device, feeds):
        """
        Preprocess EvaraDeep specific data with distance in cm

        Args:
            device: Device object with EvaraDeep configuration
            feeds: List of feed entries from ThingSpeak

        Returns:
            list: Processed entries with distance in cm (no subtraction)
        """
        processed_entries = []

        # Validate EvaraDeep configuration
        if not device.distance_field:
            return processed_entries

        # Extract distance values from all feeds
        distance_values = []
        feed_entries = []

        for feed in feeds:
            if not feed.get("entry_id"):
                continue

            distance = self._safe_numeric(feed.get(device.distance_field))

            # Keep missing distance as None — see _preprocess_evaratank for why.
            distance_values.append(distance)
            feed_entries.append(feed)

        if not feed_entries:
            return processed_entries

        # Apply filtering if configured (trailing window across calls — see
        # _apply_streaming_filter).
        if device.filtering_method in ("median", "average"):
            window = device.filter_window or 5
            filtered_distances = self._apply_streaming_filter(
                device_id, "distance", distance_values, window, device.filtering_method
            )
        else:
            filtered_distances = distance_values

        # Process each feed with filtered values
        for i, feed in enumerate(feed_entries):
            if i >= len(filtered_distances):
                continue

            # Nothing usable reported this tick — don't emit a fabricated reading
            if filtered_distances[i] is None:
                continue

            processed = {
                "Distance": round(
                    filtered_distances[i], 2
                ),  # Distance in cm (raw, no subtraction)
                "LCT": (
                    self._parse_timestamp(feed.get("created_at"))
                    if feed.get("created_at")
                    else None
                ),
                "entry_id": feed.get("entry_id"),
            }

            # Add location if available
            if device.latitude is not None:
                processed["Latitude"] = device.latitude
            if device.longitude is not None:
                processed["Longitude"] = device.longitude

            processed_entries.append(processed)

        return processed_entries

    def _preprocess_evaratds(self, device_id, device, feeds):
        """
        Preprocess EvaraTDS specific data with temperature and TDS in ppm

        Args:
            device: Device object with EvaraTDS configuration
            feeds: List of feed entries from ThingSpeak

        Returns:
            list: Processed entries with temperature and TDS in ppm
        """
        processed_entries = []

        # Validate EvaraTDS configuration
        if not device.temperature_field or not device.tds_field:
            return processed_entries

        # Extract temperature and TDS values from all feeds
        temperature_values = []
        tds_values = []
        feed_entries = []

        for feed in feeds:
            if not feed.get("entry_id"):
                continue

            temperature = self._safe_numeric(feed.get(device.temperature_field))
            tds = self._safe_numeric(feed.get(device.tds_field))

            # Keep missing fields as None — see _preprocess_evaratank for why.
            temperature_values.append(temperature)
            tds_values.append(tds)
            feed_entries.append(feed)

        if not feed_entries:
            return processed_entries

        # Apply filtering if configured (trailing window across calls — see
        # _apply_streaming_filter).
        if device.filtering_method in ("median", "average"):
            window = device.filter_window or 5
            filtered_temperatures = self._apply_streaming_filter(
                device_id,
                "temperature",
                temperature_values,
                window,
                device.filtering_method,
            )
            filtered_tds = self._apply_streaming_filter(
                device_id, "tds", tds_values, window, device.filtering_method
            )
        else:
            filtered_temperatures = temperature_values
            filtered_tds = tds_values

        # Process each feed with filtered values
        for i, feed in enumerate(feed_entries):
            if i >= len(filtered_temperatures) or i >= len(filtered_tds):
                continue

            temp_val = filtered_temperatures[i]
            tds_val = filtered_tds[i]

            # Nothing usable reported this tick — don't emit a fabricated reading
            if temp_val is None and tds_val is None:
                continue

            processed = {
                "LCT": (
                    self._parse_timestamp(feed.get("created_at"))
                    if feed.get("created_at")
                    else None
                ),
                "entry_id": feed.get("entry_id"),
            }

            if temp_val is not None:
                processed["Temperature"] = round(temp_val, 2)
            if tds_val is not None:
                processed["TDS"] = round(tds_val, 2)

            # Add location if available
            if device.latitude is not None:
                processed["Latitude"] = device.latitude
            if device.longitude is not None:
                processed["Longitude"] = device.longitude

            processed_entries.append(processed)

        return processed_entries

    def _apply_temperature_compensation(self, distance, temperature):
        """
        Apply temperature compensation to distance reading.

        Uses the speed-of-sound formula: v = 331.4 + 0.6 * T (m/s)
        Real distance = Sensor Distance × (Actual Speed / Reference Speed)

        Args:
            distance: Raw distance reading (cm)
            temperature: Temperature reading (°C)

        Returns:
            float: Compensated distance (cm)
        """
        # Bounds check: skip compensation for out-of-range temperatures
        if temperature < -40 or temperature > 85:
            logger = self._get_logger()
            logger.warning(
                f"Temperature {temperature}°C out of compensation range [-40, 85], returning raw distance."
            )
            return distance

        reference_temp = 25.0
        speed_factor = (331.4 + 0.6 * temperature) / (331.4 + 0.6 * reference_temp)

        return distance * speed_factor

    def _apply_median_filter(self, values, window):
        """
        Apply median filter to smooth out noise
        Correctly calculates median of window of N data points

        Args:
            values: List of values to filter
            window: Window size for median calculation (number of data points)

        Returns:
            list: Filtered values with same length as input
        """
        if not values:
            return []

        filtered = []

        # Validate window size
        if window < 1:
            window = 1
        if window > len(values):
            window = len(values)

        for i in range(len(values)):
            if values[i] is None:
                filtered.append(None)
                continue

            # Calculate window start and end indices
            start = max(0, i - window // 2)
            end = min(len(values), i + window // 2 + 1)

            # Extract window of values and filter out None
            window_values = sorted([v for v in values[start:end] if v is not None])
            n = len(window_values)

            if n == 0:
                filtered.append(None)
                continue

            filtered.append(self._median_of(window_values))

        return filtered

    def _apply_average_filter(self, values, window):
        """
        Apply average (mean) filter to smooth out noise
        Correctly calculates average of window of N data points

        Args:
            values: List of values to filter
            window: Window size for average calculation (number of data points)

        Returns:
            list: Filtered values with same length as input
        """
        if not values:
            return []

        filtered = []

        # Validate window size
        if window < 1:
            window = 1
        if window > len(values):
            window = len(values)

        for i in range(len(values)):
            if values[i] is None:
                filtered.append(None)
                continue

            # Calculate window start and end indices
            start = max(0, i - window // 2)
            end = min(len(values), i + window // 2 + 1)

            # Extract window of values and filter out None
            window_values = [v for v in values[start:end] if v is not None]
            n = len(window_values)

            if n == 0:
                filtered.append(None)
                continue

            filtered.append(self._average_of(window_values))

        return filtered

    def _median_of(self, values):
        """Median of a non-empty list of numbers."""
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        if n % 2 == 1:
            return sorted_vals[n // 2]
        return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2

    def _average_of(self, values):
        """Arithmetic mean of a non-empty list of numbers."""
        return sum(values) / len(values)

    def _apply_streaming_filter(self, device_id, field_key, new_values, window, method):
        """
        Trailing-window median/average filter with state carried across
        calls in self._filter_history, keyed by (device_id, field_key).

        _apply_median_filter/_apply_average_filter above compute a centered
        window purely from the values passed to a single call — correct for
        a batch of historical feeds, but a no-op whenever a call only has
        one new reading (window collapses to 1), which is the normal case
        for both ThingSpeak (results=1 per fetch) and EMQX (~1 buffered
        message per instant-trigger). This filters each new value against
        up to `window` of its own most recent history instead, so the
        configured filter actually smooths consecutive readings across
        polls.

        Args:
            device_id: device identifier — part of the history key
            field_key: name of the field this history belongs to (e.g.
                'distance') — only needs to be unique within one device
            new_values: values from this call, in chronological order (may
                contain None for a missing reading)
            window: configured filter window size
            method: 'median' or 'average'

        Returns:
            list: filtered values, same length/order as new_values
        """
        window = max(1, min(window, self._MAX_FILTER_HISTORY))
        key = (str(device_id), field_key)
        filtered = []

        with self._filter_history_lock:
            history = self._filter_history.setdefault(
                key, deque(maxlen=self._MAX_FILTER_HISTORY)
            )

            for value in new_values:
                if value is None:
                    # Don't let a missing reading dilute the history with a
                    # fabricated value — just pass the gap through.
                    filtered.append(None)
                    continue

                history.append(value)
                window_values = list(history)[-window:]

                if method == "median":
                    filtered.append(self._median_of(window_values))
                else:
                    filtered.append(self._average_of(window_values))

        return filtered

    def _convert_value(self, value):
        """Convert string value to appropriate type (float, int, or string).
        Returns the original string if it cannot be parsed as a number."""
        if value is None:
            return None

        if isinstance(value, (int, float)):
            return value

        val_str = str(value).strip()
        if not val_str:
            return None

        # Try int first (more specific)
        try:
            if "." not in val_str:
                return int(val_str)
        except (ValueError, TypeError):
            pass

        # Try float
        try:
            return float(val_str)
        except (ValueError, TypeError):
            pass

        return val_str

    def _safe_numeric(self, value):
        """Convert value to a numeric type (int or float). Returns None for non-numeric values.
        Use this instead of _convert_value when the result will be used in math operations.
        """
        result = self._convert_value(value)
        if isinstance(result, (int, float)):
            return result
        return None

    def _parse_timestamp(self, timestamp_str):
        """Parse ThingSpeak timestamp to ISO format"""
        # ThingSpeak format: 2026-04-28T12:00:00Z
        try:
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            return dt.isoformat()
        except (ValueError, TypeError):
            # Return original if parsing fails
            return timestamp_str

    def transform_to_ctop_format(self, device_id, processed_data, device_data=None):
        """
        Transform processed data to CTOP-compatible format

        Args:
            device_id: ID of the device
            processed_data: List of processed entries
            device_data: Optional device dictionary (for Firestore mode)

        Returns:
            list: Transformed data in CTOP format
        """
        if device_data:
            from models.dataclass_models import DeviceModel

            device = DeviceModel.from_dict(device_data)
        else:
            device = db.session.get(Device, device_id)

        if not device:
            return []

        known_device_types = {
            "EvaraTank",
            "EvaraFlow",
            "EvaraValve",
            "EvaraDeep",
            "EvaraTDS",
        }
        if device.device_type not in known_device_types:
            # Matches the equivalent warning in preprocess_data() for the
            # same condition — previously this silently produced an empty
            # {} payload per entry, sent to CTOP with nothing surfaced
            # anywhere explaining why.
            self._get_logger().warning(
                f"Unknown device_type '{device.device_type}' for device {device_id} — "
                f"transform_to_ctop_format() will emit empty payloads."
            )

        transformed = []

        for entry in processed_data:
            ctop_payload = {}

            # Map fields based on device type (only essential sensor fields for CTOP)
            if device.device_type == "EvaraTank":
                # EvaraTank: Level -> water_level, Temperature -> temperature
                if entry.get("Level") is not None:
                    ctop_payload["water_level"] = entry.get("Level")
                if entry.get("Temperature") is not None:
                    ctop_payload["temperature"] = entry.get("Temperature")

            elif device.device_type == "EvaraFlow":
                # EvaraFlow CTOP schema (domain: water_flow, sensor: retrofit-sensor)
                # CTOP expects: flow_rate (m³/hr, float) and waterconsumption (kl, float)
                if entry.get("FlowRate") is not None:
                    ctop_payload["flow_rate"] = entry.get("FlowRate")
                if entry.get("MeterReading") is not None:
                    ctop_payload["waterconsumption"] = entry.get("MeterReading")

            elif device.device_type == "EvaraValve":
                # EvaraValve: FlowRate -> flow_rate, Liters -> liters
                if entry.get("FlowRate") is not None:
                    ctop_payload["flow_rate"] = entry.get("FlowRate")
                if entry.get("Liters") is not None:
                    ctop_payload["liters"] = entry.get("Liters")

            elif device.device_type == "EvaraDeep":
                # EvaraDeep: Distance -> distance
                if entry.get("Distance") is not None:
                    ctop_payload["distance"] = entry.get("Distance")

            elif device.device_type == "EvaraTDS":
                # EvaraTDS: Temperature -> temperature, TDS -> tds
                if entry.get("Temperature") is not None:
                    ctop_payload["temperature"] = entry.get("Temperature")
                if entry.get("TDS") is not None:
                    ctop_payload["tds"] = entry.get("TDS")

            # Only essential sensor fields are sent to CTOP
            # No timestamp, latitude, longitude - matching user's exact requirement
            transformed.append(ctop_payload)

        return transformed

    def store_processed_data(
        self, device_id, raw_data, processed_data, transformed_data, device_data=None
    ):
        """
        Store processed data in database (optional).
        In Firestore mode, storage is handled by the local device store — skip SQLite writes.

        Args:
            device_id: ID of the device
            raw_data: Original ThingSpeak data
            processed_data: Preprocessed data
            transformed_data: CTOP-transformed data
            device_data: Optional device dictionary (for Firestore mode)
        """
        logger = self._get_logger()

        if device_data:
            # Firestore mode: processed data tracking is handled by local_device_store
            # (entry_id updates, stats). No SQLite storage needed.
            logger.debug(
                f"[Firestore mode] Skipping SQLite storage for device {device_id}"
            )
            return

        device = db.session.get(Device, device_id)
        if not device:
            return

        try:
            # Map feeds by entry_id for accurate storage
            feeds_by_id = {
                str(f.get("entry_id")): f
                for f in raw_data.get("feeds", [])
                if f.get("entry_id")
            }

            for i, entry in enumerate(processed_data):
                entry_id = str(entry.get("entry_id"))
                raw_feed = feeds_by_id.get(entry_id)

                processed = ProcessedData(
                    device_id=device_id,
                    thingspeak_entry_id=entry_id,
                    thingspeak_timestamp=(
                        datetime.fromisoformat(entry.get("LCT"))
                        if entry.get("LCT")
                        else None
                    ),
                    raw_data=json.dumps(raw_feed) if raw_feed else None,
                    processed_data=json.dumps(entry),
                    transformed_data=(
                        json.dumps(transformed_data[i])
                        if i < len(transformed_data)
                        else None
                    ),
                    processing_status="processed",
                )
                db.session.add(processed)

            db.session.commit()

        except Exception as e:
            if db.session:
                db.session.rollback()
            logger.error(
                f"Error storing processed data for device {device_id}: {str(e)}"
            )

    def _log_preprocess(self, device, processed_count, skipped_count, error):
        """Log preprocessing attempt locally"""
        logger = self._get_logger()
        device_id = str(getattr(device, "id", "unknown"))

        if error is None:
            logger.info(
                f"PREPROCESS [Device {device_id}]: Processed {processed_count} entries, skipped {skipped_count}."
            )
        else:
            logger.error(f"PREPROCESS ERROR [Device {device_id}]: {error}")

    def _get_logger(self):
        """Get logger instance"""
        import logging

        return logging.getLogger(__name__)
