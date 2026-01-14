# Core libraries
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from scipy.signal import savgol_filter


# Configuration parameters
SINUSOIDAL_SEARCH_WINDOW_SEC = 12.0  # Neighborhood search window (key parameter)
MIN_TIME_SEPARATION_SEC = 30.0      # Minimum time between cycle boundaries
MIN_CYCLES_REQUIRED = 1             # Minimum cycles needed for successful detection
MAX_LIQUIDS_TO_ANALYZE = 100          # Number of liquids to process per dataset
TRIM_DURATION_SECONDS = 100          # Seconds to trim from data edges
FIGURE_SIZE = (28, 14)             # Visualization size

# # # Dataset configuration
# # DATA_FILES = {
# #     "2-Roller Pump": "Data/viscosity_sensor_calibration/roller2_dataset.csv",
# #     "3-Roller Pump": "Data/viscosity_sensor_calibration/roller3_dataset.csv", 
# #     "4-Roller Pump": "Data/viscosity_sensor_calibration/roller4_dataset.csv"
# # }

# # # Liquid metadata for visualization
# # LIQUIDS_INFO = {
# #     'CF955_flow_ml_min': {'name': 'CF955', 'viscosity': '1 cP'},
# #     'F50_flow_ml_min': {'name': 'F50', 'viscosity': '50 cP'},
# #     'F100_flow_ml_min': {'name': 'F100', 'viscosity': '100 cP'},
# #     'F200_flow_ml_min': {'name': 'F200', 'viscosity': '200 cP'},
# #     'F350_flow_ml_min': {'name': 'F350', 'viscosity': '350 cP'},
# #     'F500_flow_ml_min': {'name': 'F500', 'viscosity': '500 cP'},
# #     'F1000_flow_ml_min': {'name': 'F1000', 'viscosity': '1000 cP'},
# #     'F5000_flow_ml_min': {'name': 'F5000', 'viscosity': '5000 cP'},
# #     'F12500_flow_ml_min': {'name': 'F12500', 'viscosity': '12500 cP'}
# # }


# ===================================================================
# DATA LOADING & PREPROCESSING
# ===================================================================

def load_and_preprocess_data(file_path, dataset_name, trim_seconds=TRIM_DURATION_SECONDS):
    """Load and preprocess pump data with adaptive trimming"""
    df = pd.read_csv(file_path)
        
    # Adaptive trimming for edge effects
    time_data = df['time_s'].values
    valid_mask = ~np.isnan(time_data)
                
    valid_time = time_data[valid_mask]
    time_start, time_end = np.min(valid_time), np.max(valid_time)
    total_duration = time_end - time_start
        
    # Adaptive trimming
    if total_duration <= 2 * trim_seconds + 60:
        actual_trim = max(5, (total_duration - 60) // 2)
    else:
        actual_trim = trim_seconds
        
    # Apply trimming
    trim_mask = (time_data >= time_start + actual_trim) & (time_data <= time_end - actual_trim)
    df_trimmed = df[trim_mask].reset_index(drop=True)
       
    return df_trimmed


# # # Load all datasets
# # datasets = {}
# # for name, file_path in DATA_FILES.items():
# #     datasets[name] = load_and_preprocess_data(file_path, name)


# ===================================================================
# SINUSOIDAL CYCLE DETECTION FUNCTION
# ===================================================================

def detect_cycles_sinusoidal(time, flow, search_window_sec=SINUSOIDAL_SEARCH_WINDOW_SEC, min_separation_sec=MIN_TIME_SEPARATION_SEC):
    """
    Enhanced sinusoidal cycle detection with neighborhood search
    
    Algorithm:
    1. Fit sinusoidal function: f(t) = A*sin(ωt + φ) + C
    2. Calculate theoretical minima from fitted function
    3. Search for actual minima within neighborhood windows around theoretical points
    4. Create cycles from consecutive refined boundaries
    5. Apply Min-Max normalization to scale both time and flow to range [0, 1] for each cycle
    
    Returns: cycles list with comprehensive metadata and Min-Max normalized time and flow
    """
    # Data validation and cleaning
    valid_mask = ~np.isnan(flow) & ~np.isnan(time)
    if np.sum(valid_mask) < 10:
        return []
    
    valid_time = time[valid_mask]
    valid_flow = flow[valid_mask]
    sort_indices = np.argsort(valid_time)
    valid_time = valid_time[sort_indices]
    valid_flow = valid_flow[sort_indices]
    
    # 1. Sinusoidal fitting
    def sinusoidal_function(t, A, omega, phi, C):
        return A * np.sin(omega * t + phi) + C
    
    # Parameter initialization
    C_init = np.mean(valid_flow)
    A_init = (np.max(valid_flow) - np.min(valid_flow)) / 2
    
    # FFT-based frequency estimation
    detrended_flow = valid_flow - C_init
    dt = np.mean(np.diff(valid_time))
    freqs = np.fft.fftfreq(len(detrended_flow), dt)
    fft_values = np.abs(np.fft.fft(detrended_flow))
    
    positive_freqs = freqs[1:len(freqs)//2]
    positive_fft = fft_values[1:len(fft_values)//2]
    
    if len(positive_freqs) > 0:
        dominant_freq = positive_freqs[np.argmax(positive_fft)]
        omega_init = 2 * np.pi * dominant_freq
    else:
        time_range = np.max(valid_time) - np.min(valid_time)
        omega_init = 2 * np.pi * 3 / time_range  # Assume ~3 cycles
    
    # Curve fitting with bounds
    bounds = ([-np.inf, 0.01, -2*np.pi, -np.inf], [np.inf, 10.0, 2*np.pi, np.inf])
    fitted_params, _ = curve_fit(
        sinusoidal_function, valid_time, valid_flow,
        p0=[A_init, omega_init, 0, C_init], bounds=bounds, maxfev=5000
    )
    
    A_fit, omega_fit, phi_fit, C_fit = fitted_params
    fitted_curve = sinusoidal_function(valid_time, *fitted_params)
    
    # Calculate fit quality
    ss_res = np.sum((valid_flow - fitted_curve) ** 2)
    ss_tot = np.sum((valid_flow - np.mean(valid_flow)) ** 2)
    r_squared = 1 - (ss_res / ss_tot)
    fitted_period = 2 * np.pi / omega_fit
    
    # print(f"Fitted sinusoid: Period={fitted_period:.1f}s, R²={r_squared:.3f}")

    
    # 2. Calculate theoretical minima
    if A_fit > 0:
        base_phase = -np.pi/2 - phi_fit
    else:
        base_phase = np.pi/2 - phi_fit
    
    time_start, time_end = np.min(valid_time), np.max(valid_time)
    theoretical_minima = []
    
    # Calculate dynamic search range based on data duration and fitted period
    total_duration = time_end - time_start
    estimated_num_cycles = int(total_duration / fitted_period) + 2  # Add buffer of 2 cycles
    search_range = max(estimated_num_cycles, 20)  # Ensure minimum search range of 20
    
    k = -search_range  # Dynamic search range for theoretical points
    while k <= search_range:
        t_min = (base_phase + 2 * np.pi * k) / omega_fit
        if time_start - fitted_period <= t_min <= time_end + fitted_period:
            theoretical_minima.append(t_min)
        k += 1
    
    theoretical_minima = sorted(theoretical_minima)
    
    # 3. Neighborhood search for actual minima
    actual_minima = []
    for theoretical_time in theoretical_minima:
        window_start = theoretical_time - search_window_sec / 2
        window_end = theoretical_time + search_window_sec / 2
        
        window_mask = (valid_time >= window_start) & (valid_time <= window_end)
        window_indices = np.where(window_mask)[0]
        
        if len(window_indices) == 0:
            continue
        
        # Find actual minimum in window
        window_flow = valid_flow[window_indices]
        min_idx = np.argmin(window_flow)
        actual_idx = window_indices[min_idx]
        
        actual_minima.append({
            'theoretical_time': theoretical_time,
            'actual_time': valid_time[actual_idx],
            'actual_flow': valid_flow[actual_idx],
            'offset': valid_time[actual_idx] - theoretical_time,
            'data_index': actual_idx
        })
    
    # 4. Filter and create cycles
    filtered_minima = []
    for min_info in actual_minima:
        if time_start <= min_info['actual_time'] <= time_end:
            # Check separation constraint
            too_close = False
            for existing in filtered_minima:
                if abs(min_info['actual_time'] - existing['actual_time']) < min_separation_sec:
                    too_close = True
                    break
            
            if not too_close:
                filtered_minima.append(min_info)
    
    filtered_minima = sorted(filtered_minima, key=lambda x: x['actual_time'])
    
    # 5. Generate cycle objects with normalized time
    cycles = []
    for i in range(len(filtered_minima) - 1):
        start_min = filtered_minima[i]
        end_min = filtered_minima[i + 1]
        
        start_time, end_time = start_min['actual_time'], end_min['actual_time']
        cycle_mask = (valid_time >= start_time) & (valid_time <= end_time)
        cycle_time_data = valid_time[cycle_mask]
        cycle_flow_data = valid_flow[cycle_mask]
        
        if len(cycle_time_data) < 3:
            continue
        
        # Apply Min-Max normalization to scale time to range [0, 1]
        time_min = np.min(cycle_time_data)
        time_max = np.max(cycle_time_data)
        if time_max > time_min:  # Avoid division by zero
            normalized_cycle_time = (cycle_time_data - time_min) / (time_max - time_min)
        else:
            normalized_cycle_time = np.zeros_like(cycle_time_data)  # All zeros if no time variation
        
        # Apply Min-Max normalization to scale flow to range [0, 1]
        flow_min = np.min(cycle_flow_data)
        flow_max = np.max(cycle_flow_data)
        if flow_max > flow_min:  # Avoid division by zero
            normalized_cycle_flow = (cycle_flow_data - flow_min) / (flow_max - flow_min)
        else:
            normalized_cycle_flow = np.zeros_like(cycle_flow_data)  # All zeros if no flow variation
        
        # Find peak in cycle
        peak_idx = np.argmax(cycle_flow_data)
        
        # Comprehensive cycle metadata with normalized time and flow
        cycle_info = {
            'cycle_number': len(cycles) + 1,
            'start_time': start_time,  # Keep original absolute time for reference
            'peak_time': cycle_time_data[peak_idx],  # Keep original absolute peak time
            'end_time': end_time,  # Keep original absolute time for reference
            'duration': end_time - start_time,
            'amplitude': cycle_flow_data[peak_idx] - min(start_min['actual_flow'], end_min['actual_flow']),
            'peak_flow': cycle_flow_data[peak_idx],
            'start_flow': start_min['actual_flow'],
            'end_flow': end_min['actual_flow'],
            'cycle_time': normalized_cycle_time.copy(),  # NORMALIZED time [0, 1]
            'cycle_flow': normalized_cycle_flow.copy(),  # NORMALIZED flow [0, 1]
            'original_cycle_time': cycle_time_data.copy(),  # Keep original for reference
            'original_cycle_flow': cycle_flow_data.copy(),  # Keep original for reference
            'data_points': len(cycle_time_data),
            'fitted_period': fitted_period,
            'fit_r_squared': r_squared,
            'start_offset': start_min['offset'],
            'end_offset': end_min['offset'],
            'search_window_sec': search_window_sec,
            'detection_method': 'Sinusoidal Fit + Neighborhood Search',
            'normalized_peak_time': normalized_cycle_time[peak_idx],  # Peak time in normalized scale
            'normalized_peak_flow': normalized_cycle_flow[peak_idx],  # Peak flow in normalized scale
            'flow_range': (flow_min, flow_max),  # Original flow range for denormalization
            'time_range': (time_min, time_max)   # Original time range for denormalization
        }
        cycles.append(cycle_info)
    
    return cycles


# ===================================================================
# CYCLE PHASE DETECTION FUNCTION
# ===================================================================

def detect_cycle_phases(cycle_data, phase1_percent=15, phase4_percent=10):
    """
    Detect 4 phases within a single cycle based on time percentages and median
    
    Parameters:
    - cycle_data: Single cycle from detect_cycles_sinusoidal result
    - phase1_percent: Percentage for phase 1 (default 20%)
    - phase4_percent: Percentage for phase 4 (default 30%) 
    
    Returns:
    - Dictionary with phase information including start/end indices and timing
    """
    cycle_time = cycle_data['cycle_time']
    cycle_flow = cycle_data['cycle_flow']
    
    if len(cycle_time) < 4:
        return None
        
    # Calculate time-based boundaries
    total_duration = cycle_time[-1] - cycle_time[0]
    phase1_duration = total_duration * (phase1_percent / 100)
    phase4_duration = total_duration * (phase4_percent / 100)
    
    # Find indices for phase boundaries
    phase1_end_time = cycle_time[0] + phase1_duration
    phase4_start_time = cycle_time[-1] - phase4_duration
    
    # Find closest indices
    phase1_end_idx = np.argmin(np.abs(cycle_time - phase1_end_time))
    phase4_start_idx = np.argmin(np.abs(cycle_time - phase4_start_time))
    
    # Find median index for phase 3 start
    median_idx = len(cycle_time) // 2
    
    # Ensure proper ordering: phase1_end < median < phase4_start
    if phase1_end_idx >= median_idx:
        phase1_end_idx = max(0, median_idx - 1)
    if phase4_start_idx <= median_idx:
        phase4_start_idx = min(len(cycle_time) - 1, median_idx + 1)
    
    # Define phase boundaries
    phases = {
        'phase1_start': 0,
        'phase1_end': phase1_end_idx,
        'phase2_start': phase1_end_idx + 1,
        'phase2_end': median_idx - 1,
        'phase3_start': median_idx,
        'phase3_end': phase4_start_idx - 1,
        'phase4_start': phase4_start_idx,
        'phase4_end': len(cycle_time) - 1,
        'peak_idx': np.argmax(cycle_flow),
        'total_points': len(cycle_time),
        'phase1_percent': phase1_percent,
        'phase4_percent': phase4_percent
    }
    
    # Calculate phase durations and statistics
    for phase_num in range(1, 5):
        start_key = f'phase{phase_num}_start'
        end_key = f'phase{phase_num}_end'
        
        if phases[end_key] >= phases[start_key]:
            start_idx = phases[start_key]
            end_idx = phases[end_key]
            
            phase_time_data = cycle_time[start_idx:end_idx+1]
            phase_flow_data = cycle_flow[start_idx:end_idx+1]
            
            phases[f'phase{phase_num}_duration'] = phase_time_data[-1] - phase_time_data[0] if len(phase_time_data) > 1 else 0
            phases[f'phase{phase_num}_points'] = len(phase_time_data)
            phases[f'phase{phase_num}_avg_flow'] = np.mean(phase_flow_data) if len(phase_flow_data) > 0 else 0
            phases[f'phase{phase_num}_max_flow'] = np.max(phase_flow_data) if len(phase_flow_data) > 0 else 0
            phases[f'phase{phase_num}_min_flow'] = np.min(phase_flow_data) if len(phase_flow_data) > 0 else 0
        else:
            # Handle edge case where phase boundaries don't make sense
            phases[f'phase{phase_num}_duration'] = 0
            phases[f'phase{phase_num}_points'] = 0
            phases[f'phase{phase_num}_avg_flow'] = 0
            phases[f'phase{phase_num}_max_flow'] = 0
            phases[f'phase{phase_num}_min_flow'] = 0
    
    return phases



# ===================================================================
# SAVITZKY-GOLAY SMOOTHING FUNCTION
# ===================================================================

def Savitzky_Golay_filter(pipeline_results, window_length=11, polyorder=3):
    """
    Apply Savitzky-Golay filter to smooth the flow data in all detected cycles
    
    Parameters:
    - pipeline_results: The results dictionary from signal_cycles function
    - window_length: Length of the filter window (must be odd and > polyorder)
    - polyorder: Order of the polynomial used to fit the samples
    
    Returns:
    - Modified pipeline_results with smoothed cycle data
    """
    
    smoothed_results = pipeline_results.copy()
    total_cycles_smoothed = 0
    
    for dataset_name, dataset_data in smoothed_results['datasets'].items():
        
        dataset_cycles_smoothed = 0
        
        for liquid_name, liquid_result in dataset_data['liquid_results'].items():
            if liquid_result['successful'] and liquid_result['cycles']:
                liquid_cycles_smoothed = 0
                
                for i, cycle in enumerate(liquid_result['cycles']):
                    if 'cycle_flow' in cycle and len(cycle['cycle_flow']) > window_length:
                        # Apply Savitzky-Golay filter to the cycle flow data
                        original_flow = cycle['cycle_flow']
                        smoothed_flow = savgol_filter(original_flow, window_length, polyorder)
                        
                        # Update the cycle with smoothed data
                        cycle['cycle_flow_original'] = original_flow.copy()  # Keep original
                        cycle['cycle_flow'] = smoothed_flow
                        cycle['smoothed'] = True
                        cycle['smooth_params'] = {'window_length': window_length, 'polyorder': polyorder}
                        
                        liquid_cycles_smoothed += 1
                        total_cycles_smoothed += 1

                    else:
                        # Cycle too short for smoothing or missing flow data
                        cycle['smoothed'] = False
                        if 'cycle_flow' in cycle:
                            cycle['cycle_flow_original'] = cycle['cycle_flow'].copy()
                
                dataset_cycles_smoothed += liquid_cycles_smoothed
    
    # Add smoothing info to results summary
    smoothed_results['smoothing_applied'] = {
        'total_cycles_smoothed': total_cycles_smoothed,
        'window_length': window_length,
        'polyorder': polyorder,
        'timestamp': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    return smoothed_results


# ===================================================================
# MAIN PIPELINE EXECUTION
# ===================================================================

def signal_cycles(datasets_dict, 
                  sinusoidal_search_window_sec=SINUSOIDAL_SEARCH_WINDOW_SEC, 
                  min_time_separation_sec=MIN_TIME_SEPARATION_SEC, 
                  min_cycles_required=MIN_CYCLES_REQUIRED,
                  max_liquids_to_analyze=MAX_LIQUIDS_TO_ANALYZE,
                  trim_duration_seconds=TRIM_DURATION_SECONDS,
                  figure_size=FIGURE_SIZE):
    """
    Execute the complete sinusoidal cycle detection pipeline with configurable parameters
    
    Parameters:
    - datasets_dict: Dictionary containing datasets to process
    - sinusoidal_search_window_sec: Neighborhood search window (default: 12.0)
    - min_time_separation_sec: Minimum time between cycle boundaries (default: 40.0)
    - min_cycles_required: Minimum cycles needed for successful detection (default: 1)
    - max_liquids_to_analyze: Number of liquids to process per dataset (default: 100)
    - trim_duration_seconds: Seconds to trim from data edges (default: 50)
    - figure_size: Visualization size (default: (28, 14))
    
    Returns:
    - Dictionary containing pipeline results
    """
    results = {
        'parameters': {
            'search_window_sec': sinusoidal_search_window_sec,
            'min_separation_sec': min_time_separation_sec,
            'min_cycles_required': min_cycles_required,
            'max_liquids_to_analyze': max_liquids_to_analyze,
            'trim_duration_seconds': trim_duration_seconds,
            'figure_size': figure_size,
            'method': 'Enhanced Sinusoidal + Neighborhood Search'
        },
        'datasets': {},
        'summary': {'total_successful': 0, 'total_failed': 0, 'total_cycles': 0}
    }
        
    for dataset_name, df in datasets_dict.items():
        
        dataset_results = {
            'liquid_results': {},
            'successful_liquids': 0,
            'failed_liquids': 0,
            'total_cycles_detected': 0
        }
        
        # Process each liquid column
        liquid_columns = [col for col in df.columns if col.endswith('_flow_ml_min')]
        
        for liquid_col in liquid_columns[:max_liquids_to_analyze]:
            liquid_name = liquid_col.replace('_flow_ml_min', '')
            
            time_data = df['time_s'].values
            flow_data = df[liquid_col].values
            
            # Data validation
            valid_mask = ~np.isnan(time_data) & ~np.isnan(flow_data)
            if np.sum(valid_mask) < 10:
                dataset_results['liquid_results'][liquid_name] = {
                    'cycles': [], 'num_cycles': 0, 'successful': False, 'error': 'Insufficient data'
                }
                dataset_results['failed_liquids'] += 1
                continue
            
            # Run cycle detection
            cycles = detect_cycles_sinusoidal(time_data, flow_data, sinusoidal_search_window_sec, min_time_separation_sec)
            
            # Apply phase detection to each detected cycle
            cycles_with_phases = []
            for cycle in cycles:
                # Add phase information to each cycle
                phases = detect_cycle_phases(cycle)
                if phases:
                    cycle['phases'] = phases
                else:
                    cycle['phases'] = None
                cycles_with_phases.append(cycle)
            
            # Calculate statistics
            if cycles_with_phases:
                durations = [c['duration'] for c in cycles_with_phases]
                amplitudes = [c['amplitude'] for c in cycles_with_phases]
                r_squared_values = [c['fit_r_squared'] for c in cycles_with_phases]
                
                # Count cycles with successful phase detection
                cycles_with_phases_count = sum(1 for c in cycles_with_phases if c['phases'] is not None)
                
                liquid_result = {
                    'cycles': cycles_with_phases,
                    'num_cycles': len(cycles_with_phases),
                    'cycles_with_phases': cycles_with_phases_count,
                    'successful': len(cycles_with_phases) >= min_cycles_required,
                    'avg_duration': np.mean(durations),
                    'std_duration': np.std(durations),
                    'avg_amplitude': np.mean(amplitudes),
                    'fitted_period': cycles_with_phases[0]['fitted_period'],
                    'avg_fit_r_squared': np.mean(r_squared_values),
                    'flow_range': (np.min(flow_data[valid_mask]), np.max(flow_data[valid_mask])),
                    'time_range': (np.min(time_data[valid_mask]), np.max(time_data[valid_mask]))
                }
                                
                if len(cycles_with_phases) >= min_cycles_required:
                    dataset_results['successful_liquids'] += 1
                    dataset_results['total_cycles_detected'] += len(cycles_with_phases)
                    results['summary']['total_cycles'] += len(cycles_with_phases)
                else:
                    dataset_results['failed_liquids'] += 1
            else:
                liquid_result = {
                    'cycles': [], 'num_cycles': 0, 'cycles_with_phases': 0, 'successful': False,
                    'flow_range': (np.min(flow_data[valid_mask]), np.max(flow_data[valid_mask])),
                    'time_range': (np.min(time_data[valid_mask]), np.max(time_data[valid_mask]))
                }
                dataset_results['failed_liquids'] += 1
            
            dataset_results['liquid_results'][liquid_name] = liquid_result
        
        results['datasets'][dataset_name] = dataset_results
        
        # Dataset summary
        total_liquids = dataset_results['successful_liquids'] + dataset_results['failed_liquids']
        success_rate = dataset_results['successful_liquids'] / total_liquids * 100 if total_liquids > 0 else 0

    
    # Overall summary
    results['summary']['total_successful'] = sum(d['successful_liquids'] for d in results['datasets'].values())
    results['summary']['total_failed'] = sum(d['failed_liquids'] for d in results['datasets'].values())
    
    total_combinations = results['summary']['total_successful'] + results['summary']['total_failed']
    overall_success_rate = results['summary']['total_successful'] / total_combinations * 100 if total_combinations > 0 else 0
    
    print(f"Overall success: {results['summary']['total_successful']}/{total_combinations} ({overall_success_rate:.1f}%)")
    print(f"Total cycles detected: {results['summary']['total_cycles']}")
    
    # Phase detection summary
    total_cycles = results['summary']['total_cycles']
    total_cycles_with_phases = 0
    for dataset_results in results['datasets'].values():
        for liquid_result in dataset_results['liquid_results'].values():
            if 'cycles_with_phases' in liquid_result:
                total_cycles_with_phases += liquid_result['cycles_with_phases']
        
    # Apply Savitzky-Golay smoothing to the results
    results = Savitzky_Golay_filter(results)
    
    return results

# # # Execute the pipeline
# # pipeline_results = signal_cycles(datasets)



# ===================================================================
# CUSTOM SIGMOID FUNCTION FITTING FOR ALL DETECTED CYCLES
# ===================================================================

def custom_sigmoid_function(x, a1, a2, a3, a4, b1, b2, b3, b4, h1, h2, h3, h4, y_data=None):
    """
    Custom sigmoid function with mixed positive and negative sigmoid terms:
    y = h1/(1+exp(-(x-a1)/b1)) + h2/(1+exp(-(x-a2)/b2)) + h3/(1+exp(-(x-a3)/b3)) + h4/(1+exp(-(x-a4)/b4)) + c
    
    Parameters:
    - a1: position parameter for first sigmoid (has to be in phase 1)
    - a2: position parameter for second sigmoid (has to be less than median time and higher than a1)
    - a3: position parameter for third sigmoid (has to be in phase 3 and higher than a2)
    - a4: position parameter for fourth sigmoid (has to be higher than a3 and be in phase 4, a4 < 1)
    - b1, b2, b3, b4: slope parameters for the four sigmoid components (all positive and < 0.1)
    - h1, h2: positive amplitude scaling factors (< 0.1)
    - h3, h4: negative amplitude scaling factors (> -0.1)
    - y_data: the y data used to calculate c as minimum value (passed during fitting)
    
    Note: c is automatically set to min(y_data) and is not a fitted parameter
    
    Constraints:
    - a1: has to be in phase 1
    - a2: has to be less than median time and higher than a1
    - a3: has to be in phase 3 and higher than a2
    - a4: has to be higher than a3 and be in the phase 4 (a4<1)
    - b1, b2, b3, b4: all positive and < 0.1
    - h1, h2: positive and < 0.1
    - h3, h4: negative and > -0.1
    """
    # Calculate c as minimum of y_data
    if y_data is not None:
        c = np.min(y_data)
    else:
        c = 0  # Fallback if y_data not provided
    
    term1 = h1 / (1 + np.exp(-(x - a1) / b1))
    term2 = h2 / (1 + np.exp(-(x - a2) / b2))
    term3 = h3 / (1 + np.exp(-(x - a3) / b3))
    term4 = h4 / (1 + np.exp(-(x - a4) / b4))
    
    return term1 + term2 + term3 + term4 + c

def fit_custom_sigmoid_to_cycle(cycle_data, max_attempts=10):
    """
    Fit the custom sigmoid function to a single cycle with robust parameter initialization
    
    Updated Constraints:
    - a1: has to be in phase 1
    - a2: has to be less than median time and higher than a1
    - a3: has to be in phase 3 and higher than a2
    - a4: has to be higher than a3 and be in phase 4 (a4<1)
    - b1, b2, b3, b4: all positive and < 0.1
    - h1, h2: positive and < 1.0 (updated for normalized y-axis)
    - h3, h4: negative and > -1.0 (updated for normalized y-axis)
    - c: automatically set to min(cycle_flow) - not a fitted parameter
    """
    cycle_time = cycle_data['cycle_time']  # Already normalized from detect_cycles_sinusoidal
    cycle_flow = cycle_data['cycle_flow']
    
    # Get phase information if available for better positioning
    phases = cycle_data.get('phases', None)
    
    # Calculate median time for constraint
    median_time = 0.5  # Since time is normalized 0-1, median is 0.5
    
    # Create wrapper function for fitting all parameters
    def sigmoid_wrapper(x, a1, a2, a3, a4, b1, b2, b3, b4, h1, h2, h3, h4):
        return custom_sigmoid_function(x, a1, a2, a3, a4, b1, b2, b3, b4, h1, h2, h3, h4, y_data=cycle_flow)
    
    # Calculate basic statistics for parameter initialization
    flow_min = np.min(cycle_flow)
    flow_max = np.max(cycle_flow)
    flow_range = flow_max - flow_min
    
    # Setup normalized time (cycle_time should already be normalized from detect_cycles_sinusoidal)
    normalized_time = cycle_time
    
    # Get phase information if available for better positioning
    if phases:
        # Use phase boundaries for positioning (convert indices to normalized positions)
        phase1_start = phases['phase1_start'] / len(normalized_time) if len(normalized_time) > 0 else 0.0
        phase1_end = phases['phase1_end'] / len(normalized_time) if len(normalized_time) > 0 else 0.25
        phase3_start = phases.get('phase3_start', phases['phase2_end']) / len(normalized_time) if len(normalized_time) > 0 else 0.5
        phase3_end = phases.get('phase3_end', phases['phase4_start']) / len(normalized_time) if len(normalized_time) > 0 else 0.75
        phase4_start = phases['phase4_start'] / len(normalized_time) if len(normalized_time) > 0 else 0.75
        phase4_end = phases['phase4_end'] / len(normalized_time) if len(normalized_time) > 0 else 1.0
    else:
        # Fallback to default phase estimates
        phase1_start, phase1_end = 0.0, 0.25
        phase3_start, phase3_end = 0.5, 0.75
        phase4_start, phase4_end = 0.75, 1.0
        
    # Find peak location for better initialization
    peak_idx = np.argmax(cycle_flow)
    peak_time = normalized_time[peak_idx]
    
    for attempt in range(max_attempts):
        try:
            # Adaptive parameter initialization - gets more flexible with each attempt
            noise_factor = 0.005 + 0.005 * attempt  # Smaller noise for tight constraints
            
            # Initialize positions with constraints
            # a1: has to be in phase 1
            a1_init = phase1_start + (phase1_end - phase1_start) * np.random.random() + np.random.normal(0, noise_factor)
            a1_init = max(phase1_start, min(phase1_end, a1_init))
            
            # a2: has to be less than median time and higher than a1
            a2_max = min(median_time, phase3_start)
            a2_init = a1_init + 0.01 + (a2_max - a1_init - 0.01) * np.random.random() + np.random.normal(0, noise_factor)
            a2_init = max(a1_init + 0.01, min(a2_max, a2_init))
            
            # a3: has to be in phase 3 and higher than a2
            a3_init = max(a2_init + 0.01, phase3_start) + (phase3_end - max(a2_init + 0.01, phase3_start)) * np.random.random() + np.random.normal(0, noise_factor)
            a3_init = max(a2_init + 0.01, min(phase3_end, a3_init))
            
            # a4: has to be higher than a3 and be in the phase 4 (a4<1)
            a4_init = max(a3_init + 0.01, phase4_start) + (0.99 - max(a3_init + 0.01, phase4_start)) * np.random.random() + np.random.normal(0, noise_factor)
            a4_init = max(a3_init + 0.01, min(0.99, a4_init))
            
            # Initialize slope parameters (< 0.1)
            base_slope = 0.01 + 0.05 * np.random.random()  # 0.01-0.06 range
            b1_init = max(0.001, min(0.099, base_slope + np.random.normal(0, 0.01)))
            b2_init = max(0.001, min(0.099, base_slope + np.random.normal(0, 0.01)))
            b3_init = max(0.001, min(0.099, base_slope + np.random.normal(0, 0.01)))
            b4_init = max(0.001, min(0.099, base_slope + np.random.normal(0, 0.01)))
            
            # Initialize Amplitudes h1, h2 (positive), h3, h4 (negative) - Updated for normalized data
            base_amplitude = 0.1 + 0.4 * np.random.random()  # 0.1-0.5 range for normalized data
            h1_init = max(0.01, min(0.99, base_amplitude + np.random.normal(0, 0.05)))
            h2_init = max(0.01, min(0.99, base_amplitude + np.random.normal(0, 0.05)))
            h3_init = max(-0.99, min(-0.01, -base_amplitude + np.random.normal(0, 0.05)))
            h4_init = max(-0.99, min(-0.01, -base_amplitude + np.random.normal(0, 0.05)))
            
            # Set bounds for all parameters
            bounds_lower = [
                phase1_start,  # a1: in phase 1
                max(a1_init + 0.001, phase1_start + 0.001),  # a2: higher than a1
                max(phase3_start, a2_init + 0.001),  # a3: in phase 3 and higher than a2
                max(phase4_start, a3_init + 0.001),  # a4: in phase 4 and higher than a3
                0.001, 0.001, 0.001, 0.001,  # b1, b2, b3, b4: positive
                0.01, 0.01, -0.99, -0.99   # h1, h2: positive, h3, h4: negative
            ]
            
            bounds_upper = [
                phase1_end,  # a1: in phase 1
                min(median_time, phase3_start - 0.001),  # a2: less than median time
                min(phase3_end, phase4_start - 0.001),  # a3: in phase 3
                0.99,  # a4: < 1
                0.099, 0.099, 0.099, 0.099,  # b1, b2, b3, b4: < 0.1
                0.99, 0.99, -0.01, -0.01   # h1, h2: < 1.0, h3, h4: > -1.0
            ]
            
            bounds = (bounds_lower, bounds_upper)
            
            # Initial parameter vector (all 12 parameters)
            p0 = [a1_init, a2_init, a3_init, a4_init, b1_init, b2_init, b3_init, b4_init, h1_init, h2_init, h3_init, h4_init]
            
            # Perform curve fitting
            fitted_params, covariance = curve_fit(
                sigmoid_wrapper, 
                normalized_time, 
                cycle_flow,
                p0=p0,
                bounds=bounds,
                maxfev=8000,
                method='trf'
            )
            
            # Extract fitted parameters
            a1_fit, a2_fit, a3_fit, a4_fit = fitted_params[:4]
            b1_fit, b2_fit, b3_fit, b4_fit = fitted_params[4:8]
            h1_fit, h2_fit, h3_fit, h4_fit = fitted_params[8:12]
            c_fit = flow_min
            
            # Validate constraints
            phase1_ok = phase1_start <= a1_fit <= phase1_end
            a2_ok = a1_fit < a2_fit < median_time
            phase3_ok = phase3_start <= a3_fit <= phase3_end and a2_fit < a3_fit
            phase4_ok = phase4_start <= a4_fit < 1.0 and a3_fit < a4_fit
            ordering_ok = a1_fit < a2_fit < a3_fit < a4_fit
            slopes_ok = all(0 < p < 0.1 for p in [b1_fit, b2_fit, b3_fit, b4_fit])
            amplitudes_ok = (0 < h1_fit < 1.0 and 0 < h2_fit < 1.0 and 
                           -1.0 < h3_fit < 0 and -1.0 < h4_fit < 0)
            
            constraints_ok = phase1_ok and a2_ok and phase3_ok and phase4_ok and ordering_ok and slopes_ok and amplitudes_ok
            
            if not constraints_ok:
                raise ValueError()
            
            # Calculate fit quality using the fitted values
            fitted_values = custom_sigmoid_function(normalized_time, a1_fit, a2_fit, a3_fit, a4_fit, b1_fit, b2_fit, b3_fit, b4_fit, h1_fit, h2_fit, h3_fit, h4_fit, y_data=cycle_flow)
            ss_res = np.sum((cycle_flow - fitted_values) ** 2)
            ss_tot = np.sum((cycle_flow - np.mean(cycle_flow)) ** 2)
            r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
            
            # Create parameter errors array
            param_errors = np.full(13, np.nan)  # 13 total parameters including c
            if covariance is not None:
                fitted_errors = np.sqrt(np.diag(covariance))
                param_errors[:12] = fitted_errors  # First 12 are fitted parameters
                param_errors[12] = 0.0  # c is fixed
            
            # Print successful fit info for debugging
            if attempt > 0:
                print(f"   ✅ Success on attempt {attempt + 1}: R²={r_squared:.3f}, constraints satisfied")
            
            # Combine all parameters in correct order: [a1, a2, a3, a4, b1, b2, b3, b4, h1, h2, h3, h4, c]
            all_fitted_params = np.array([a1_fit, a2_fit, a3_fit, a4_fit, b1_fit, b2_fit, b3_fit, b4_fit, h1_fit, h2_fit, h3_fit, h4_fit, c_fit])
            
            return {
                'success': True,
                'fitted_params': all_fitted_params,
                'param_names': ['a1', 'a2', 'a3', 'a4', 'b1', 'b2', 'b3', 'b4', 'h1', 'h2', 'h3', 'h4', 'c'],
                'param_errors': param_errors,
                'fitted_values': fitted_values,
                'normalized_time': normalized_time,  # Already normalized
                'original_time': cycle_data.get('original_cycle_time', cycle_time),  # Use original if available
                'original_flow': cycle_flow,
                'r_squared': r_squared,
                'attempt': attempt + 1,
                'c_fixed': True,
                'c_value': c_fit
            }
            
        except Exception as e:
            if attempt == max_attempts - 1:
                print(f"   ❌ All {max_attempts} attempts failed. Last error: {str(e)[:150]}")
                return {
                    'success': False,
                    'error': str(e),
                    'normalized_time': normalized_time,  # Already normalized
                    'original_time': cycle_data.get('original_cycle_time', cycle_time),
                    'original_flow': cycle_flow,
                    'attempts_made': max_attempts
                }
            continue

def fit_sigmoid_to_all_cycles(pipeline_results):
    
    fitting_results = {
        'datasets': {},
        'summary': {
            'total_cycles': 0,
            'successful_fits': 0,
            'failed_fits': 0,
            'success_rate': 0.0
        }
    }
    
    for dataset_name, dataset_data in pipeline_results['datasets'].items():  
        dataset_fitting = {
            'liquids': {},
            'successful_fits': 0,
            'failed_fits': 0,
            'total_cycles': 0
        }
        
        for liquid_name, liquid_result in dataset_data['liquid_results'].items():
            if liquid_result['successful'] and liquid_result['cycles']:
                liquid_fitting = {
                    'cycles': [],
                    'successful_fits': 0,
                    'failed_fits': 0
                }
                                
                for cycle_idx, cycle in enumerate(liquid_result['cycles']):
                    fit_result = fit_custom_sigmoid_to_cycle(cycle)
                    fit_result['cycle_number'] = cycle_idx + 1
                    fit_result['liquid_name'] = liquid_name
                    fit_result['dataset_name'] = dataset_name
                    
                    liquid_fitting['cycles'].append(fit_result)
                    dataset_fitting['total_cycles'] += 1
                    fitting_results['summary']['total_cycles'] += 1
                    
                    if fit_result['success']:
                        liquid_fitting['successful_fits'] += 1
                        dataset_fitting['successful_fits'] += 1
                        fitting_results['summary']['successful_fits'] += 1
                    else:
                        liquid_fitting['failed_fits'] += 1
                        dataset_fitting['failed_fits'] += 1
                        fitting_results['summary']['failed_fits'] += 1
                
                # Calculate liquid-level statistics
                total_liquid_cycles = liquid_fitting['successful_fits'] + liquid_fitting['failed_fits']
                liquid_success_rate = liquid_fitting['successful_fits'] / total_liquid_cycles * 100 if total_liquid_cycles > 0 else 0
                
                
                dataset_fitting['liquids'][liquid_name] = liquid_fitting
            
            else:
                print(f"{liquid_name}: No cycles to fit")
        
        fitting_results['datasets'][dataset_name] = dataset_fitting
        
        # Dataset summary
        dataset_success_rate = dataset_fitting['successful_fits'] / dataset_fitting['total_cycles'] * 100 if dataset_fitting['total_cycles'] > 0 else 0
    # Calculate overall summary
    fitting_results['summary']['success_rate'] = fitting_results['summary']['successful_fits'] / fitting_results['summary']['total_cycles'] * 100 if fitting_results['summary']['total_cycles'] > 0 else 0
    
    print(f"   📈 Overall success rate: {fitting_results['summary']['success_rate']:.1f}%")
   
    return fitting_results

# # sigmoid_fitting_results = fit_sigmoid_to_all_cycles(pipeline_results)




# ===================================================================
# COMPREHENSIVE CYCLE DATASET CREATION
# ===================================================================

def create_comprehensive_cycle_dataset(pipeline_results, sigmoid_results, LIQUIDS_INFO):
    """
    Create a comprehensive dataset with all cycle features:
    - a1, a2, a3, a4 (sigmoid position parameters)
    - h1, h2, h3, h4 (sigmoid amplitude parameters)
    - b1, b2, b3, b4 (sigmoid slope parameters)
    - sample name
    - pump rollers (number of rollers: 2, 3, or 4)
    - cycle number
    - duration_sec (un-normalized, in seconds)
    - flow_max_ml_min, flow_min_ml_min (un-normalized, in ml/min)
    - viscosity of the sample (cP) - this is the label
    """
    
    all_cycle_data = []
    
    for dataset_name, dataset_data in pipeline_results['datasets'].items():
               
        for liquid_name, liquid_result in dataset_data['liquid_results'].items():
            if not (liquid_result['successful'] and liquid_result['cycles']):
                continue
                
            # Get viscosity value from LIQUIDS_INFO
            liquid_key = f"{liquid_name}_flow_ml_min"
            if liquid_key in LIQUIDS_INFO:
                viscosity_str = LIQUIDS_INFO[liquid_key]['viscosity']
                # Extract numeric viscosity value (remove 'cP' and convert to float)
                viscosity_cp = float(viscosity_str.replace(' cP', ''))
            else:
                print(f"   ⚠️ Viscosity info not found for {liquid_name}, skipping...")
                continue
            
            # Get sigmoid fitting results for this liquid if available
            sigmoid_available = False
            sigmoid_cycles = []
            if (dataset_name in sigmoid_results['datasets'] and 
                liquid_name in sigmoid_results['datasets'][dataset_name]['liquids']):
                sigmoid_cycles = sigmoid_results['datasets'][dataset_name]['liquids'][liquid_name]['cycles']
                sigmoid_available = True
            
            # print(f"   🎯 {liquid_name} ({viscosity_cp} cP): {len(liquid_result['cycles'])} cycles...", end=' ')
            
            cycles_processed = 0
            cycles_with_sigmoid = 0
            
            for cycle_idx, cycle in enumerate(liquid_result['cycles']):
                # Get sigmoid parameters if available for this cycle
                sigmoid_params = None
                if sigmoid_available and cycle_idx < len(sigmoid_cycles):
                    sigmoid_fit = sigmoid_cycles[cycle_idx]
                    if sigmoid_fit['success']:
                        sigmoid_params = sigmoid_fit['fitted_params']
                        cycles_with_sigmoid += 1
                
                # Calculate un-normalized duration, max, and min from cycle data
                cycle_time = cycle['cycle_time']
                cycle_flow = cycle['cycle_flow']
                
                # Get original (un-normalized) data if available
                if 'original_cycle_time' in cycle:
                    original_time = cycle['original_cycle_time']
                    original_flow = cycle.get('original_cycle_flow', cycle_flow)
                else:
                    # Use current data as fallback
                    original_time = cycle_time
                    original_flow = cycle_flow
                
                # Calculate features with proper units
                duration_sec = float(original_time[-1] - original_time[0]) if len(original_time) > 1 else 0.0
                flow_max_ml_min = float(np.max(original_flow))
                flow_min_ml_min = float(np.min(original_flow))
                
                # Create cycle record
                cycle_record = {
                    'sample_name': liquid_name,
                    'pump_rollers': 4, 
                    'cycle_number': cycle_idx + 1,
                    'duration_sec': duration_sec,
                    'flow_max_ml_min': flow_max_ml_min,
                    'flow_min_ml_min': flow_min_ml_min,
                    'viscosity_cp': viscosity_cp,
                    'has_sigmoid_fit': sigmoid_params is not None
                }
                
                # Add sigmoid parameters if available
                if sigmoid_params is not None:
                    param_names = ['a1', 'a2', 'a3', 'a4', 'b1', 'b2', 'b3', 'b4', 'h1', 'h2', 'h3', 'h4']
                    for i, param_name in enumerate(param_names):
                        cycle_record[param_name] = float(sigmoid_params[i])
                else:
                    # Fill with NaN if sigmoid fitting not available
                    param_names = ['a1', 'a2', 'a3', 'a4', 'b1', 'b2', 'b3', 'b4', 'h1', 'h2', 'h3', 'h4']
                    for param_name in param_names:
                        cycle_record[param_name] = np.nan
                
                all_cycle_data.append(cycle_record)
                cycles_processed += 1
            
            #print(f"✅ {cycles_processed} cycles, {cycles_with_sigmoid} with sigmoid fits")
    
    # Convert to DataFrame
    cycle_dataset = pd.DataFrame(all_cycle_data)
    
    # Check if has_sigmoid_fit column should be removed (if all values are True)
    if cycle_dataset['has_sigmoid_fit'].all():
        cycle_dataset = cycle_dataset.drop('has_sigmoid_fit', axis=1)
        include_sigmoid_fit = False
    else:
        include_sigmoid_fit = True
    
    # Reorder columns for better readability
    column_order = [
        'sample_name', 'pump_rollers', 'cycle_number',
        'a1', 'a2', 'a3', 'a4',
        'h1', 'h2', 'h3', 'h4', 
        'b1', 'b2', 'b3', 'b4',
        'duration_sec', 'flow_max_ml_min', 'flow_min_ml_min',
        'viscosity_cp'
    ]
    
    if include_sigmoid_fit:
        column_order.append('has_sigmoid_fit')
    
    cycle_dataset = cycle_dataset[column_order]
    
    print(f"\n📊 DATASET CREATION SUMMARY:")
    print(f"   🎯 Total cycles: {len(cycle_dataset)}")
    print(f"   📈 Unique samples: {cycle_dataset['sample_name'].nunique()}")
    if include_sigmoid_fit:
        print(f"Cycles with sigmoid fits: {cycle_dataset['has_sigmoid_fit'].sum()}")
    else:
        print(f"All cycles have sigmoid fits")    
    return cycle_dataset

# ===================================================================
# PLOTTING FUNCTION FOR SINGLE SIGNAL WITH CYCLES

def plot_single_signal_with_cycles(ax, pump_data, liquid_type, liquid_name, cycles_data, pump_type, 
                                  liquid_info, show_legend=False):    
    # Define alternating colors for cycles
    cycle_colors = ['#4285F4', '#FFA500']
    
    # First plot the background signal in light gray
    if liquid_type in pump_data.columns:
        time_data = pump_data['time_s'].dropna()
        flowrate_data = pump_data[liquid_type].dropna()
        
        # Ensure same length
        min_len = min(len(time_data), len(flowrate_data))
        time_data = time_data.iloc[:min_len]
        flowrate_data = flowrate_data.iloc[:min_len]
        
        # Plot background signal
        ax.plot(time_data, flowrate_data, color='#3C4043', linewidth=1, alpha=0.5, label='Background Signal')
    
    # Plot each cycle in alternating colors
    total_cycles_for_signal = 0
    
    if (pump_type in cycles_data['datasets'] and 
        'liquid_results' in cycles_data['datasets'][pump_type] and 
        liquid_name in cycles_data['datasets'][pump_type]['liquid_results']):
        
        liquid_cycles_info = cycles_data['datasets'][pump_type]['liquid_results'][liquid_name]
        
        if 'cycles' in liquid_cycles_info and liquid_cycles_info['cycles']:
            detected_cycles = liquid_cycles_info['cycles']
            total_cycles_for_signal = len(detected_cycles)
            
            # Plot each cycle in alternating colors
            for cycle_idx, cycle_data in enumerate(detected_cycles):
                color = cycle_colors[cycle_idx % 2]  # Alternate between blue and orange
                
                # Extract cycle time and flowrate data using correct keys
                if 'original_cycle_time' in cycle_data and 'original_cycle_flow' in cycle_data:
                    cycle_time = cycle_data['original_cycle_time']
                    cycle_flowrate = cycle_data['original_cycle_flow']
                    
                    # Plot cycle with alternating colors
                    if cycle_idx == 0 and show_legend:
                        # First blue cycle - add to legend
                        ax.plot(cycle_time, cycle_flowrate, color= '#4285F4', linewidth=1.5, 
                               alpha=0.9, zorder=10, label='Cycles (Blue)')
                    elif cycle_idx == 1 and show_legend:
                        # First orange cycle - add to legend
                        ax.plot(cycle_time, cycle_flowrate, color='#FFA500', linewidth=1.5, 
                               alpha=0.9, zorder=10, label='Cycles (Orange)')
                    else:
                        # Subsequent cycles - no label
                        ax.plot(cycle_time, cycle_flowrate, color=color, linewidth=1.5, 
                               alpha=0.9, zorder=10)
                    
                    # Add cycle number annotation
                    if len(cycle_time) > 0:
                        mid_idx = len(cycle_time) // 2
                        ax.annotate(f'C{cycle_idx+1}', 
                                   (cycle_time[mid_idx], cycle_flowrate[mid_idx]), 
                                   xytext=(0, 10), textcoords='offset points',
                                   fontsize=9, color=color, fontweight='bold',
                                   ha='center', va='bottom',
                                   bbox=dict(boxstyle="round,pad=0.2", facecolor='white', alpha=0.8))
    
    # Formatting for the subplot
    display_name = f"{pump_type} - {liquid_info['name']} ({liquid_info['viscosity']})"
    ax.set_title(f'{display_name} - {total_cycles_for_signal} cycles detected', 
                 fontweight='bold', fontsize=11)
    ax.set_xlabel('Time (s)', fontsize=10)
    ax.set_ylabel('Flowrate (ml/min)', fontsize=10)
    ax.grid(True, alpha=0.3)
    
    # Add legend if requested
    if show_legend and total_cycles_for_signal > 0:
        ax.legend(loc='upper right', fontsize=9)
    
    return total_cycles_for_signal

def create_average_cycles_from_signal_cycles(cycles_data, pump_type=None, liquids_info=None):
    
    def create_single_average_cycle(liquid_data):
        """Helper function to create average cycle for a single liquid."""
        if not ('cycles' in liquid_data and liquid_data['cycles'] and liquid_data.get('successful', False)):
            return None
        
        # Get all cycles for this liquid
        all_cycles = liquid_data['cycles']
        
        # Extract flow and time data from each cycle
        cycle_flows = []
        cycle_times = []
        
        for cycle in all_cycles:
            if 'cycle_flow' in cycle and 'cycle_time' in cycle:
                cycle_flows.append(cycle['cycle_flow'])
                cycle_times.append(cycle['cycle_time'])
        
        if not cycle_flows:
            return None
        
        # Find the maximum length among all cycles
        max_length = max(len(cycle_flow) for cycle_flow in cycle_flows)
        
        # Normalize all cycles to the same length using interpolation
        normalized_flows = []
        normalized_times = []
        
        for cycle_flow, cycle_time in zip(cycle_flows, cycle_times):
            if len(cycle_flow) < max_length:
                # Create normalized time axis from 0 to 1
                x_old = np.linspace(0, 1, len(cycle_flow))
                x_new = np.linspace(0, 1, max_length)
                
                # Interpolate flow and time data
                flow_interpolated = np.interp(x_new, x_old, cycle_flow)
                time_interpolated = np.interp(x_new, x_old, cycle_time)
                
                normalized_flows.append(flow_interpolated)
                normalized_times.append(time_interpolated)
            else:
                normalized_flows.append(cycle_flow)
                normalized_times.append(cycle_time)
        
        # Calculate the average cycle
        avg_flow = np.mean(normalized_flows, axis=0)
        avg_time = np.mean(normalized_times, axis=0)
        
        return {
            'avg_flow': avg_flow,
            'avg_time': avg_time,
            'num_cycles': len(all_cycles),
            'max_length': max_length,
            'original_cycles': len(cycle_flows),
            'flow_std': np.std(normalized_flows, axis=0),
            'time_std': np.std(normalized_times, axis=0)
        }
    
    # Main processing
    average_cycles = {}
    
    # Determine which pump types to process
    pump_types_to_process = [pump_type] if pump_type else list(cycles_data['datasets'].keys())
    
    for current_pump in pump_types_to_process:
        if current_pump in cycles_data['datasets']:
            average_cycles[current_pump] = {}
            pump_data = cycles_data['datasets'][current_pump]
            
            if 'liquid_results' in pump_data:
                for liquid_name, liquid_data in pump_data['liquid_results'].items():
                    avg_cycle_result = create_single_average_cycle(liquid_data)
                    
                    if avg_cycle_result is not None:
                        average_cycles[current_pump][liquid_name] = avg_cycle_result
                        print(f"  {current_pump} - {liquid_name}: Created average cycle from {avg_cycle_result['original_cycles']} individual cycles (length: {avg_cycle_result['max_length']})")
                    else:
                        print(f"  {current_pump} - {liquid_name}: No valid cycles found")
    
    return average_cycles



# ===================================================================
# AVERAGE CYCLE DATASET CREATION
# ===================================================================

def create_average_cycle_dataset(average_cycles_data, average_sigmoid_results, liquids_info):
    """
    Create a comprehensive dataset with all average cycle features:
    - a1, a2, a3, a4 (sigmoid position parameters)
    - h1, h2, h3, h4 (sigmoid amplitude parameters)
    - b1, b2, b3, b4 (sigmoid slope parameters)
    - sample name
    - pump_type (the pump type identifier)
    - num_original_cycles (number of cycles averaged)
    - duration_sec (average duration in seconds)
    - flow_max_ml_min, flow_min_ml_min (average max/min flow in ml/min)
    - flow_std_avg (average standard deviation across the cycle)
    - viscosity of the sample (cP) - this is the label
    """
    
    all_average_cycle_data = []
    
    for pump_type, pump_cycles in average_cycles_data.items():
        
        for liquid_name, cycle_data in pump_cycles.items():
            # Get viscosity value from LIQUIDS_INFO
            liquid_key = f"{liquid_name}_flow_ml_min"
            if liquid_key in liquids_info:
                viscosity_str = liquids_info[liquid_key]['viscosity']
                # Extract numeric viscosity value (remove 'cP' and convert to float)
                viscosity_cp = float(viscosity_str.replace(' cP', ''))
            else:
                print(f"   ⚠️ Viscosity info not found for {liquid_name}, skipping...")
                continue
            
            # Get sigmoid fitting results for this liquid if available
            sigmoid_params = None
            sigmoid_available = False
            if (pump_type in average_sigmoid_results and 
                liquid_name in average_sigmoid_results[pump_type]):
                sigmoid_data = average_sigmoid_results[pump_type][liquid_name]
                if sigmoid_data.get('success', False):
                    sigmoid_params = sigmoid_data['sigmoid_params']
                    sigmoid_available = True
            
            # Extract average cycle data
            avg_flow = cycle_data['avg_flow']
            avg_time = cycle_data['avg_time']
            flow_std = cycle_data['flow_std']
            num_cycles = cycle_data['num_cycles']
            
            # Calculate features
            duration_sec = float(avg_time[-1] - avg_time[0]) if len(avg_time) > 1 else 0.0
            flow_max_ml_min = float(np.max(avg_flow))
            flow_min_ml_min = float(np.min(avg_flow))
            flow_std_avg = float(np.mean(flow_std))  # Average standard deviation across the cycle
            
            # Create average cycle record
            cycle_record = {
                'sample_name': liquid_name,
                'pump_type': pump_type,
                'num_original_cycles': num_cycles,
                'duration_sec': duration_sec,
                'flow_max_ml_min': flow_max_ml_min,
                'flow_min_ml_min': flow_min_ml_min,
                'flow_std_avg': flow_std_avg,
                'viscosity_cp': viscosity_cp,
                'has_sigmoid_fit': sigmoid_available
            }
            
            # Add sigmoid parameters if available
            if sigmoid_params is not None:
                param_names = ['a1', 'a2', 'a3', 'a4', 'b1', 'b2', 'b3', 'b4', 'h1', 'h2', 'h3', 'h4']
                for i, param_name in enumerate(param_names):
                    cycle_record[param_name] = float(sigmoid_params[i])
                    
                # Add R² and RMSE from sigmoid fitting
                if 'r_squared' in sigmoid_data:
                    cycle_record['r_squared'] = float(sigmoid_data['r_squared'])
                if 'rmse' in sigmoid_data:
                    cycle_record['rmse'] = float(sigmoid_data['rmse'])
            else:
                # Fill with NaN if sigmoid fitting not available
                param_names = ['a1', 'a2', 'a3', 'a4', 'b1', 'b2', 'b3', 'b4', 'h1', 'h2', 'h3', 'h4']
                for param_name in param_names:
                    cycle_record[param_name] = np.nan
                cycle_record['r_squared'] = np.nan
                cycle_record['rmse'] = np.nan
            
            all_average_cycle_data.append(cycle_record)
    
    # Convert to DataFrame
    avg_cycle_dataset = pd.DataFrame(all_average_cycle_data)
    
    # Check if has_sigmoid_fit column should be removed (if all values are True)
    if avg_cycle_dataset['has_sigmoid_fit'].all():
        avg_cycle_dataset = avg_cycle_dataset.drop('has_sigmoid_fit', axis=1)
        include_sigmoid_fit = False
    else:
        include_sigmoid_fit = True
    
    # Reorder columns for better readability
    column_order = [
        'sample_name', 'pump_type', 'num_original_cycles',
        'a1', 'a2', 'a3', 'a4',
        'h1', 'h2', 'h3', 'h4', 
        'b1', 'b2', 'b3', 'b4',
        'r_squared', 'rmse',
        'duration_sec', 'flow_max_ml_min', 'flow_min_ml_min', 'flow_std_avg',
        'viscosity_cp'
    ]
    
    if include_sigmoid_fit:
        column_order.append('has_sigmoid_fit')
    
    # Only include columns that exist in the DataFrame
    existing_columns = [col for col in column_order if col in avg_cycle_dataset.columns]
    avg_cycle_dataset = avg_cycle_dataset[existing_columns]
    
    print(f"\n📊 AVERAGE CYCLE DATASET CREATION SUMMARY:")
    print(f"   🎯 Total average cycles: {len(avg_cycle_dataset)}")
    print(f"   📈 Unique samples: {avg_cycle_dataset['sample_name'].nunique()}")
    print(f"   🔧 Unique pump types: {avg_cycle_dataset['pump_type'].nunique()}")
    if include_sigmoid_fit:
        print(f"   📈 Average cycles with sigmoid fits: {avg_cycle_dataset['has_sigmoid_fit'].sum()}")
    else:
        print(f"   📈 All average cycles have sigmoid fits")
    
    return avg_cycle_dataset



# ===================================================================
# PLOTTING FUNCTION FOR SINGLE SIGNAL WITH SIGMOID FITS

def plot_single_signal_with_sigmoid_fits(ax, pump_data, liquid_type, liquid_name, cycles, sigmoid_fit, 
                                        pump_type, liquid_info, show_legend=True):
        
    # Plot the original signal using actual time values if available
    signal_data = pump_data[liquid_type]
    
    # Check if we have time_s column for actual time values
    if 'time_s' in pump_data.columns:
        time_values = pump_data['time_s'].values
        ax.plot(time_values, signal_data.values, '#3C4043', alpha=0.5, linewidth=1, 
                label='Flow Signal' if show_legend else '')
        xlabel = 'Time (s)'
    else:
        # Fallback to index if no time column
        ax.plot(signal_data.index, signal_data.values, '#3C4043', alpha=0.5, linewidth=1, 
                label='Flow Signal' if show_legend else '')
        xlabel = 'Time Index'
        time_values = signal_data.index.values
    
    # Track cycles with sigmoid fits
    cycles_with_fits = 0
    
    # Check if we have cycle detection and sigmoid fit results
    if (pump_type in cycles['datasets'] and 
        'liquid_results' in cycles['datasets'][pump_type] and 
        liquid_name in cycles['datasets'][pump_type]['liquid_results']):
        
        liquid_results = cycles['datasets'][pump_type]['liquid_results'][liquid_name]
        
        # Plot cycle boundaries using actual time if available
        if 'cycle_indices' in liquid_results:
            cycle_indices = liquid_results['cycle_indices']
            
            # Plot vertical lines for cycle boundaries
            for i, (start, end) in enumerate(cycle_indices):
                # Map indices to time values
                start_time = time_values[start] if start < len(time_values) else start
                end_time = time_values[end] if end < len(time_values) else end
                
                color = '#0F9D58' if i == 0 else '#0F9D58'
                alpha = 0.6 if i == 0 else 0.3
                ax.axvline(start_time, color=color, linestyle='--', alpha=alpha, 
                          label='Cycle Boundaries' if i == 0 and show_legend else '')
                ax.axvline(end_time, color=color, linestyle='--', alpha=alpha)
    
    # Get original cycle data to extract denormalization parameters
    original_cycles = []
    if (pump_type in cycles['datasets'] and 
        'liquid_results' in cycles['datasets'][pump_type] and 
        liquid_name in cycles['datasets'][pump_type]['liquid_results']):
        
        liquid_results = cycles['datasets'][pump_type]['liquid_results'][liquid_name]
        if 'cycles' in liquid_results:
            original_cycles = liquid_results['cycles']
    
    # Check if we have sigmoid fits for this liquid
    if ('datasets' in sigmoid_fit and 
        pump_type in sigmoid_fit['datasets'] and 
        'liquids' in sigmoid_fit['datasets'][pump_type] and 
        liquid_name in sigmoid_fit['datasets'][pump_type]['liquids']):
        
        liquid_sigmoid_data = sigmoid_fit['datasets'][pump_type]['liquids'][liquid_name]
        
        # Plot sigmoid fits for each cycle
        if 'cycles' in liquid_sigmoid_data:
            sigmoid_cycles_list = liquid_sigmoid_data['cycles']
            
            # Iterate through the list of sigmoid fit results
            for i, sigmoid_cycle_data in enumerate(sigmoid_cycles_list):
                if (sigmoid_cycle_data.get('success', False) and 
                    'fitted_values' in sigmoid_cycle_data and
                    'normalized_time' in sigmoid_cycle_data and
                    i < len(original_cycles)):
                    
                    # Get the corresponding original cycle data for denormalization
                    original_cycle = original_cycles[i]
                    
                    # Get the normalization parameters from the original cycle
                    if ('time_range' in original_cycle and 'flow_range' in original_cycle):
                        time_min, time_max = original_cycle['time_range']
                        flow_min, flow_max = original_cycle['flow_range']
                        
                        # Get normalized coordinates
                        normalized_time = sigmoid_cycle_data['normalized_time']
                        normalized_fitted_values = sigmoid_cycle_data['fitted_values']
                        
                        # Denormalize both time and flow coordinates
                        denormalized_time = normalized_time * (time_max - time_min) + time_min
                        denormalized_flow = normalized_fitted_values * (flow_max - flow_min) + flow_min
                        
                        # Plot the un-normalized sigmoid fit using actual denormalized time values
                        is_first_sigmoid = cycles_with_fits == 0
                        ax.plot(denormalized_time, denormalized_flow, '#0F9D58', linewidth=1.5, alpha=0.8,
                               label='Sigmoid Fits' if is_first_sigmoid and show_legend else '')
                        
                        cycles_with_fits += 1
    
    # Set title and labels
    ax.set_title(f"{pump_type} - {liquid_info['name']} ({liquid_info['viscosity']}) - Sigmoid Fits", 
                fontweight='bold', fontsize=10)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel('Flow Rate (ml/min)', fontsize=9)
    
    # Add grid
    ax.grid(True, alpha=0.3)
    
    # Add legend if requested and this is the first subplot
    if show_legend:
        ax.legend(loc='upper right', fontsize=8)
    
    # Add text annotation with cycle count
    ax.text(0.02, 0.98, f'Sigmoid Fits: {cycles_with_fits}', 
           transform=ax.transAxes, fontsize=8, verticalalignment='top',
           bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8))
    
    return cycles_with_fits

# Create the comprehensive dataset
# # comprehensive_cycle_dataset = create_comprehensive_cycle_dataset(pipeline_results, sigmoid_fitting_results)