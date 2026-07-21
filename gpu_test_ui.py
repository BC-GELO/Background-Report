#!/usr/bin/env python3
"""
GPU Performance and Health Test UI

A web-based user interface for testing GPU performance and health.
Built with Gradio, using the existing hardware_health_checker.py backend.

Features:
- Quick GPU health check (specs, temperature, utilization)
- GPU stress test with configurable duration
- Performance benchmark (CUDA matrix operations)
- Real-time monitoring during tests
- Detailed reports with pass/fail status

Run: python gpu_test_ui.py
"""

import gradio as gr
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Dict, Optional, Tuple

# Import the existing hardware checker
try:
    from hardware_health_checker import HardwareChecker, StressTester
except ImportError:
    print("Error: hardware_health_checker.py not found in current directory")
    sys.exit(1)


class GPUTester:
    """GPU-specific testing functionality extracted from HardwareChecker."""
    
    def __init__(self):
        self.checker = HardwareChecker()
        self.stress_tester = StressTester()
        self._stop_flag = threading.Event()
    
    def get_gpu_info(self) -> Dict:
        """Get current GPU information."""
        return self.checker.check_gpu()
    
    def run_quick_health_check(self) -> Tuple[str, str]:
        """Run a quick GPU health check without stress."""
        try:
            gpu_info = self.get_gpu_info()
            
            report = []
            report.append("=" * 60)
            report.append("GPU HEALTH CHECK REPORT")
            report.append("=" * 60)
            report.append(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            report.append("")
            
            # Basic Info
            report.append("-" * 60)
            report.append("GPU INFORMATION")
            report.append("-" * 60)
            report.append(f"Name: {gpu_info.get('name', 'Unknown')}")
            report.append(f"Vendor: {gpu_info.get('vendor', 'Unknown')}")
            report.append(f"Driver Version: {gpu_info.get('driver', 'Unknown')}")
            
            if gpu_info.get('memory_mb'):
                report.append(f"VRAM: {gpu_info['memory_mb']} MB")
            
            report.append("")
            
            # Current Status
            report.append("-" * 60)
            report.append("CURRENT STATUS")
            report.append("-" * 60)
            
            temp = gpu_info.get('temperature')
            util = gpu_info.get('utilization')
            
            if temp is not None:
                report.append(f"Temperature: {temp}°C")
                if temp > 85:
                    report.append("  ⚠️ WARNING: High temperature!")
                elif temp > 70:
                    report.append("  ℹ️ Temperature is elevated but acceptable")
                else:
                    report.append("  ✓ Temperature is normal")
            else:
                report.append("Temperature: Not available (install nvidia-smi for monitoring)")
            
            if util is not None:
                report.append(f"Utilization: {util}%")
            else:
                report.append("Utilization: Not available")
            
            report.append("")
            
            # Health Assessment
            report.append("-" * 60)
            report.append("HEALTH ASSESSMENT")
            report.append("-" * 60)
            
            issues = gpu_info.get('issues', [])
            health_status = gpu_info.get('health_status', 'unknown')
            
            if health_status == 'good':
                report.append("Status: ✓ GOOD - No issues detected")
            elif health_status == 'warning':
                report.append("Status: ⚠️ WARNING - Some concerns found")
            elif health_status == 'critical':
                report.append("Status: ❌ CRITICAL - Immediate attention required")
            else:
                report.append("Status: ℹ️ UNKNOWN - Unable to determine health status")
            
            if issues:
                report.append("")
                report.append("Issues Found:")
                for issue in issues:
                    if 'Critical' in issue:
                        report.append(f"  ❌ {issue}")
                    elif 'Warning' in issue:
                        report.append(f"  ⚠️ {issue}")
                    else:
                        report.append(f"  ℹ️ {issue}")
            
            report.append("")
            report.append("=" * 60)
            
            return "\n".join(report), "success"
            
        except Exception as e:
            error_msg = f"❌ Error during health check: {str(e)}"
            return error_msg, "error"
    
    def run_stress_test(self, duration: int, progress=gr.Progress()) -> Tuple[str, str]:
        """Run GPU stress test with progress tracking."""
        try:
            self._stop_flag.clear()
            
            report = []
            report.append("=" * 60)
            report.append("GPU STRESS TEST")
            report.append("=" * 60)
            report.append(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            report.append(f"Duration: {duration} seconds")
            report.append("")
            
            # Get initial GPU info
            initial_info = self.get_gpu_info()
            report.append("-" * 60)
            report.append("INITIAL STATE")
            report.append("-" * 60)
            report.append(f"GPU: {initial_info.get('name', 'Unknown')}")
            if initial_info.get('temperature'):
                report.append(f"Initial Temperature: {initial_info['temperature']}°C")
            report.append("")
            
            # Run the stress test
            report.append("-" * 60)
            report.append("RUNNING STRESS TEST")
            report.append("-" * 60)
            
            # Use the existing stress tester
            result = self.stress_tester.stress_gpu(duration)
            
            report.append("")
            report.append("-" * 60)
            report.append("RESULTS")
            report.append("-" * 60)
            
            compute_load = result.get('compute_load_generated', False)
            report.append(f"Compute Load Generated: {'Yes ✓' if compute_load else 'No (monitoring only)'}")
            
            if result.get('samples_collected', 0) > 0:
                report.append(f"Monitoring Samples: {result['samples_collected']}")
                
                if result.get('max_temp_c'):
                    report.append(f"Max Temperature: {result['max_temp_c']}°C")
                    if result['max_temp_c'] > 90:
                        report.append("  ❌ CRITICAL: Temperature exceeded safe limits!")
                    elif result['max_temp_c'] > 83:
                        report.append("  ⚠️ WARNING: High temperature under load")
                    else:
                        report.append("  ✓ Temperature within acceptable range")
                
                if result.get('avg_utilization_percent') is not None:
                    report.append(f"Average Utilization: {result['avg_utilization_percent']}%")
            
            report.append("")
            report.append("-" * 60)
            report.append("HEALTH STATUS")
            report.append("-" * 60)
            
            health_status = result.get('health_status', 'unknown')
            if health_status == 'good':
                report.append("Status: ✓ PASSED - GPU handled stress test well")
            elif health_status == 'warning':
                report.append("Status: ⚠️ WARNING - Some concerns during stress test")
            elif health_status == 'critical':
                report.append("Status: ❌ FAILED - Critical issues detected")
            else:
                report.append("Status: ℹ️ UNKNOWN")
            
            issues = result.get('issues', [])
            if issues:
                report.append("")
                report.append("Issues:")
                for issue in issues:
                    if 'Critical' in issue:
                        report.append(f"  ❌ {issue}")
                    elif 'Warning' in issue:
                        report.append(f"  ⚠️ {issue}")
                    else:
                        report.append(f"  ℹ️ {issue}")
            
            report.append("")
            report.append("=" * 60)
            report.append(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            report.append("=" * 60)
            
            return "\n".join(report), "success"
            
        except KeyboardInterrupt:
            return "Stress test interrupted by user.", "interrupted"
        except Exception as e:
            return f"❌ Error during stress test: {str(e)}", "error"
    
    def run_performance_benchmark(self, iterations: int = 10) -> Tuple[str, str]:
        """Run GPU performance benchmark using matrix operations."""
        try:
            import torch
            
            if not torch.cuda.is_available():
                return (
                    "❌ CUDA not available.\n\n"
                    "To run GPU benchmarks:\n"
                    "1. Ensure you have an NVIDIA GPU\n"
                    "2. Install CUDA drivers\n"
                    "3. Install PyTorch with CUDA support:\n"
                    "   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118",
                    "no_cuda"
                )
            
            report = []
            report.append("=" * 60)
            report.append("GPU PERFORMANCE BENCHMARK")
            report.append("=" * 60)
            report.append(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            report.append(f"Iterations: {iterations}")
            report.append("")
            
            # Device info
            device = torch.device('cuda')
            gpu_name = torch.cuda.get_device_name(0)
            gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            
            report.append("-" * 60)
            report.append("GPU SPECIFICATIONS")
            report.append("-" * 60)
            report.append(f"Name: {gpu_name}")
            report.append(f"Total VRAM: {gpu_memory:.2f} GB")
            report.append(f"CUDA Cores: ~{torch.cuda.get_device_properties(0).multi_processor_count * 128}")
            report.append("")
            
            # Benchmark parameters
            sizes = [1024, 2048, 4096]
            results = []
            
            report.append("-" * 60)
            report.append("MATRIX MULTIPLICATION BENCHMARK")
            report.append("-" * 60)
            report.append("Testing different matrix sizes (A @ B where A,B are N×N)")
            report.append("")
            
            for size in sizes:
                times = []
                for _ in range(iterations):
                    start = time.perf_counter()
                    a = torch.randn((size, size), device=device)
                    b = torch.randn((size, size), device=device)
                    c = a @ b
                    torch.cuda.synchronize()
                    elapsed = time.perf_counter() - start
                    times.append(elapsed)
                
                avg_time = sum(times) / len(times)
                min_time = min(times)
                max_time = max(times)
                
                # Calculate TFLOPS (2 * N^3 operations for matrix multiplication)
                ops = 2 * (size ** 3)
                tflops = ops / (avg_time * 1e12)
                
                results.append({
                    'size': size,
                    'avg_ms': avg_time * 1000,
                    'min_ms': min_time * 1000,
                    'max_ms': max_time * 1000,
                    'tflops': tflops
                })
                
                report.append(f"Matrix Size: {size}×{size}")
                report.append(f"  Average: {avg_time*1000:.2f} ms ({tflops:.2f} TFLOPS)")
                report.append(f"  Min: {min_time*1000:.2f} ms | Max: {max_time*1000:.2f} ms")
                report.append("")
            
            # Memory bandwidth test
            report.append("-" * 60)
            report.append("MEMORY BANDWIDTH TEST")
            report.append("-" * 60)
            
            mem_size = int(min(gpu_memory * 0.5, 4) * 1024 * 1024 * 1024)  # Up to 4GB or 50% of VRAM
            tensor_size = int((mem_size / 4) ** 0.5)  # Float32 = 4 bytes
            
            write_times = []
            read_times = []
            
            for _ in range(3):
                # Write test
                torch.cuda.synchronize()
                start = time.perf_counter()
                a = torch.randn((tensor_size, tensor_size), device=device)
                torch.cuda.synchronize()
                write_times.append(time.perf_counter() - start)
                
                # Read test
                start = time.perf_counter()
                _ = a.cpu()
                torch.cuda.synchronize()
                read_times.append(time.perf_counter() - start)
            
            data_bytes = tensor_size * tensor_size * 4  # Float32
            write_bw = data_bytes / (sum(write_times) / len(write_times)) / 1e9  # GB/s
            read_bw = data_bytes / (sum(read_times) / len(read_times)) / 1e9  # GB/s
            
            report.append(f"Test Size: {data_bytes / 1e6:.2f} MB")
            report.append(f"Write Bandwidth: {write_bw:.2f} GB/s")
            report.append(f"Read Bandwidth: {read_bw:.2f} GB/s")
            report.append("")
            
            # Summary
            report.append("-" * 60)
            report.append("PERFORMANCE SUMMARY")
            report.append("-" * 60)
            
            avg_tflops = sum(r['tflops'] for r in results) / len(results)
            
            if avg_tflops > 10:
                rating = "✓ EXCELLENT - High-performance GPU"
            elif avg_tflops > 5:
                rating = "✓ GOOD - Capable GPU for most tasks"
            elif avg_tflops > 1:
                rating = "⚠️ MODERATE - Entry-level or older GPU"
            else:
                rating = "⚠️ LOW - Limited compute capability"
            
            report.append(f"Average Performance: {avg_tflops:.2f} TFLOPS")
            report.append(f"Rating: {rating}")
            report.append("")
            report.append("=" * 60)
            report.append(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            report.append("=" * 60)
            
            return "\n".join(report), "success"
            
        except ImportError:
            return (
                "❌ PyTorch not installed.\n\n"
                "Install PyTorch for GPU benchmarking:\n"
                "pip install torch torchvision",
                "no_torch"
            )
        except Exception as e:
            return f"❌ Error during benchmark: {str(e)}", "error"


def create_ui():
    """Create the Gradio interface."""
    tester = GPUTester()
    
    with gr.Blocks(title="GPU Performance & Health Tester") as ui:
        gr.Markdown("""
        # 🖥️ GPU Performance & Health Tester
        
        Test your GPU's performance, monitor temperatures, and check for stability issues.
        
        **Features:**
        - 🔍 Quick Health Check - Instant GPU status
        - 🔥 Stress Test - Push your GPU to the limit
        - ⚡ Performance Benchmark - Measure compute power
        """)
        
        with gr.Tabs():
            # Tab 1: Health Check
            with gr.TabItem("🔍 Health Check"):
                gr.Markdown("### Quick GPU Health Check")
                gr.Markdown("Get instant information about your GPU's current state, including temperature, utilization, and any potential issues.")
                
                with gr.Row():
                    check_btn = gr.Button("Run Health Check", variant="primary", size="lg")
                
                health_output = gr.Textbox(
                    label="Health Report",
                    lines=20
                )
                
                health_status = gr.Label(label="Status")
                
                check_btn.click(
                    fn=tester.run_quick_health_check,
                    inputs=[],
                    outputs=[health_output, health_status]
                )
            
            # Tab 2: Stress Test
            with gr.TabItem("🔥 Stress Test"):
                gr.Markdown("### GPU Stress Test")
                gr.Markdown("""
                **Warning:** This will put your GPU under heavy load and may cause:
                - Increased temperatures
                - Higher fan noise
                - System slowdown
                
                Make sure your system has adequate cooling before proceeding.
                """)
                
                with gr.Row():
                    stress_duration = gr.Slider(
                        minimum=30,
                        maximum=300,
                        value=60,
                        step=30,
                        label="Test Duration (seconds)",
                        info="Recommended: 60-120 seconds for quick test, 300+ for thorough testing"
                    )
                
                with gr.Row():
                    stress_btn = gr.Button("Start Stress Test", variant="stop", size="lg")
                
                stress_output = gr.Textbox(
                    label="Stress Test Report",
                    lines=25
                )
                
                stress_status = gr.Label(label="Status")
                
                stress_btn.click(
                    fn=tester.run_stress_test,
                    inputs=[stress_duration],
                    outputs=[stress_output, stress_status]
                )
            
            # Tab 3: Performance Benchmark
            with gr.TabItem("⚡ Performance Benchmark"):
                gr.Markdown("### GPU Performance Benchmark")
                gr.Markdown("""
                Measures your GPU's compute performance using matrix operations.
                Requires CUDA-capable GPU with PyTorch installed.
                
                **Tests included:**
                - Matrix multiplication (different sizes)
                - Memory bandwidth measurement
                - TFLOPS calculation
                """)
                
                with gr.Row():
                    bench_iterations = gr.Slider(
                        minimum=5,
                        maximum=50,
                        value=10,
                        step=5,
                        label="Iterations per Test",
                        info="More iterations = more accurate but slower"
                    )
                
                with gr.Row():
                    bench_btn = gr.Button("Run Benchmark", variant="primary", size="lg")
                
                bench_output = gr.Textbox(
                    label="Benchmark Report",
                    lines=30
                )
                
                bench_status = gr.Label(label="Status")
                
                bench_btn.click(
                    fn=tester.run_performance_benchmark,
                    inputs=[bench_iterations],
                    outputs=[bench_output, bench_status]
                )
        
        # Footer
        gr.Markdown("""
        ---
        **Note:** Results may vary based on system configuration, background processes, and thermal conditions.
        For best results, close other applications and ensure adequate cooling.
        """)
    
    return ui


if __name__ == "__main__":
    print("Starting GPU Performance & Health Tester UI...")
    print("Open your browser to the URL shown below.")
    print("")
    
    ui = create_ui()
    ui.launch(server_name="0.0.0.0", server_port=7860, share=False)
