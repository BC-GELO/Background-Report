# GPU Performance & Health Tester UI

A web-based user interface for testing GPU performance and health, built on top of the existing `hardware_health_checker.py` backend.

## Features

### 🔍 Quick Health Check
- Instant GPU status report
- Temperature monitoring (requires nvidia-smi)
- Utilization tracking
- Driver information
- VRAM detection
- Health assessment with pass/fail/warning status

### 🔥 Stress Test
- Configurable test duration (30-300 seconds)
- GPU compute load generation (requires PyTorch with CUDA)
- Temperature monitoring under load
- Utilization tracking during stress
- Automatic thermal throttling detection
- Detailed health report with issue identification

### ⚡ Performance Benchmark
- Matrix multiplication tests (1024×1024, 2048×2048, 4096×4096)
- TFLOPS calculation
- Memory bandwidth measurement (read/write)
- Performance rating (Excellent/Good/Moderate/Low)
- Multi-iteration averaging for accuracy

## Requirements

### Required
- Python 3.8+
- `gradio` - Web UI framework
- `psutil` - System monitoring
- `hardware_health_checker.py` - Backend (included in this repository)

### Optional (for enhanced functionality)
- **NVIDIA GPU + Drivers**: For temperature/utilization monitoring via `nvidia-smi`
- **PyTorch with CUDA**: For actual GPU compute load during stress tests and benchmarks
  ```bash
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
  ```

## Installation

```bash
# Install dependencies
pip install gradio psutil

# Optional: Install PyTorch with CUDA support for GPU benchmarks
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```

## Usage

### Start the Web Interface

```bash
python gpu_test_ui.py
```

The server will start and display a URL (typically `http://localhost:7860`). Open this URL in your web browser to access the interface.

### Access from Remote Machine

To make the UI accessible from other machines on your network:

```bash
python gpu_test_ui.py --server-name 0.0.0.0 --server-port 7860
```

Or enable public sharing:

```python
# Edit gpu_test_ui.py, change the last line to:
ui.launch(server_name="0.0.0.0", server_port=7860, share=True)
```

## Interface Overview

### Tab 1: Health Check
Click "Run Health Check" to get an instant report on your GPU's current state. This includes:
- GPU name and vendor
- Driver version
- VRAM amount
- Current temperature (if available)
- Current utilization (if available)
- Health status assessment

### Tab 2: Stress Test
1. Adjust the duration slider (recommended: 60-120 seconds for quick tests)
2. Click "Start Stress Test"
3. Monitor the real-time report showing:
   - Initial GPU state
   - Maximum temperature reached
   - Average utilization
   - Any issues detected (thermal throttling, instability, etc.)

**Warning:** Stress tests put your GPU under heavy load. Ensure adequate cooling before running extended tests.

### Tab 3: Performance Benchmark
1. Set the number of iterations (more = more accurate but slower)
2. Click "Run Benchmark"
3. View detailed performance metrics:
   - Matrix multiplication speed at different sizes
   - TFLOPS rating
   - Memory bandwidth
   - Overall performance rating

**Note:** Benchmarks require PyTorch with CUDA support. Without it, you'll see instructions for installation.

## Interpreting Results

### Health Status Indicators
- ✓ **GOOD**: No issues detected, GPU is healthy
- ⚠️ **WARNING**: Some concerns found (elevated temperatures, minor issues)
- ❌ **CRITICAL**: Immediate attention required (overheating, hardware problems)
- ℹ️ **UNKNOWN**: Unable to determine status (missing monitoring tools)

### Temperature Guidelines
- **< 70°C**: Normal operating temperature
- **70-83°C**: Elevated but acceptable under load
- **83-90°C**: High temperature, monitor closely
- **> 90°C**: Critical - risk of thermal damage

### Performance Ratings (TFLOPS)
- **> 10 TFLOPS**: Excellent - High-performance GPU
- **5-10 TFLOPS**: Good - Capable for most tasks
- **1-5 TFLOPS**: Moderate - Entry-level or older GPU
- **< 1 TFLOPS**: Low - Limited compute capability

## Troubleshooting

### "No GPU detected"
- Ensure your GPU is properly installed and drivers are up to date
- For NVIDIA GPUs, verify `nvidia-smi` works from command line

### "Temperature not available"
- Install NVIDIA drivers and ensure `nvidia-smi` is on your PATH
- On Windows, consider installing HWiNFO64 or LibreHardwareMonitor

### "CUDA not available"
- Install PyTorch with CUDA support:
  ```bash
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
  ```
- Verify CUDA installation: `nvidia-smi` should show your GPU

### "Stress test failed"
- Ensure adequate cooling
- Close other GPU-intensive applications
- Try a shorter duration first
- Check system logs for hardware errors

## Integration with Existing Tools

This UI uses the existing `hardware_health_checker.py` backend, so all GPU detection and stress testing logic is shared. Reports generated by the UI follow the same format as the command-line tool.

### Save Results
All reports can be copied directly from the text boxes in the UI. For automated reporting, use the command-line tool:

```bash
# Generate JSON reports
python hardware_health_checker.py --mode both --duration standard --yes
```

## Security Notes

- The web interface runs locally by default
- When exposing to a network, ensure proper firewall configuration
- Do not run stress tests on systems with known cooling problems
- Monitor temperatures during extended testing

## License

Same license as the parent `hardware_health_checker.py` project.

## Contributing

Feel free to enhance the UI with additional features such as:
- Real-time temperature graphs
- Historical data tracking
- Comparison with reference GPUs
- Automated testing schedules
- Alert notifications for critical temperatures
