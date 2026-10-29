"""tools 包：与原 tools.py 同名同行为，按职责分成五个分片。"""
from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from .. import external_script, measures
from ._common import (ANALYSIS_SOUND_CLASSES, CUSTOM_SCRIPT_TOOL, EMPTY_PARAMETERS, LocalEnvironment, LocalTool, MEASURE_TOOL, ObjectRow, PITCH_CEILING_BOUNDS, PITCH_FLOOR_BOUNDS, RANGE_END_KEYS, RANGE_START_KEYS, RUN_SCRIPT_TOOL, SOUND_CLASSES, TEMPORARY_OBJECT_NAME, TIME_DOMAIN_CLASSES, Tool, ToolContext, ToolError, _assemble, _auto_time_expressions, _clamp_range_lines, _clamp_time_lines, _coerce_number, _default_time_expression, _echoes_editor_selection, _finish, _formant_lines, _formant_numbers, _formants_arg, _id_fragment, _insert_boundary_block, _integer, _integer_arg, _named_target, _new_name, _normalize_text, _number, _number_arg, _object_arg, _optional_seconds, _pitch_band, _pitch_extreme_lines, _point_query_lines, _query_times, _range_argument, _range_lines, _range_note, _range_pair, _rise_onset_lines, _seconds_arg, _seconds_argument, _selection_range, _text_arg, _textgrid_row, _tier_number, _times_arg, _unit_arg, _unit_display, _unit_literal, _vot_time, _write_result, parse_object_context, praat_path, praat_text, quote)
from ._common import (MAX_POINTS, MIN_POINTS, POINT_SPACING_SECONDS, UNKNOWN_SPAN_POINTS,
                      _query_point_count, _query_seconds_range)
from .scripts import (ALIAS_ARGUMENTS, EXTERNAL_OUTPUT_LINES, FORBIDDEN_SCRIPT_PATTERNS, INFO_LINE_PATTERN, INFO_LITERAL_PATTERN, INFO_NOISE_PATTERN, INFO_OTHER_PATTERN, INFO_VALUE_PATTERN, PYTHON_SCRIPT_PATTERNS, SINGLE_QUOTED_LITERAL, WHOLE_OBJECT_ONLY, _INTERNAL_ARGUMENTS, _external_textgrid, _reject_unsupported_arguments, _run_external_script, describe_script, neutralize_info_commands, normalize_script, praat_literal, save_textgrid_script, validate_script, validate_script_source_types)
from .measurements import (MEASURE_SEPARATORS, _MEASURE_CLASS_DERIVATIONS, _MEASURE_RESERVED_KEYS, _build_duration, _build_formant_bandwidth, _build_formant_frequency, _build_formant_statistics, _build_hammarberg_index, _build_harmonicity_statistics, _build_hl_ratio, _build_intensity, _build_intensity_slope, _build_intensity_statistics, _build_measure, _build_object_info, _build_peak_to_average_ratio, _build_pitch, _build_pitch_peak_latency, _build_pitch_statistics, _build_pulses, _build_spectral_emphasis, _build_vot, _build_vot_auto, _build_vot_explicit, _measure_entries, _measure_expand, _measure_names, _measure_pattern, _measure_plan, _measure_schema, _measure_select_lines, _measure_settings, measure_parameter_help, measure_signature)
from .operations import (_build_concatenate, _build_create_sound, _build_duplicate, _build_extract_part, _build_play, _build_read_file, _build_remove, _build_rename, _build_resample, _build_save_sound, _build_select, _build_spectrogram, _build_textgrid_info, _build_textgrid_insert_boundary, _build_textgrid_set_interval, _build_view)
from .schema import (LOCAL_TOOLS, MAX_ACTIONS_PER_REQUEST, MUTATING_TOOLS, TOOLS, TOOL_MAP, TOOL_PARAMETERS, catalog_text, is_mutating, plan_actions, render, tool_labels, tool_parameters, tool_schemas)

