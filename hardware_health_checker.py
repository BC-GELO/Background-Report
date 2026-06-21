#!/usr/bin/env python3
"""
Computer Hardware Health Checker and Ranker
Checks components for specs, runtime, health, and problem reports.
Ranks overall computer condition from 1 (very poor) to 10 (perfect).
"""

import subprocess
import re
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional


class HardwareChecker:
    def __init__(self):
        self.results = {
            'timestamp': datetime.now().isoformat(),
            'components': {},
            'problems': [],
            'scores': {},
            'overall_score': 0
        }
    
    def run_command(self, command: str) -> Tuple[str, int]:
        """Execute a shell command and return output and exit code."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.stdout.strip(), result.returncode
        except subprocess.TimeoutExpired:
            return "", -1
        except Exception as e:
            return str(e), -1
    
    def check_cpu(self) -> Dict:
        """Check CPU specifications and health."""
        cpu_info = {
            'name': 'Unknown',
            'cores': 0,
            'threads': 0,
            'current_freq': 0,
            'max_freq': 0,
            'temperature': None,
            'load': None,
            'health_status': 'unknown',
            'issues': []
        }
        
        # Get CPU name and cores
        stdout, _ = self.run_command("cat /proc/cpuinfo")
        if stdout:
            model_match = re.search(r'model name\s*:\s*(.+)', stdout)
            if model_match:
                cpu_info['name'] = model_match.group(1).strip()
            
            # Count physical cores and threads
            cores = len(re.findall(r'^processor\s*:', stdout, re.MULTILINE))
            cpu_info['threads'] = cores
            
            phys_ids = set(re.findall(r'physical id\s*:\s*(\d+)', stdout))
            cpu_info['cores'] = max(len(phys_ids) * (cores // max(len(phys_ids), 1)), cores)
        
        # Get frequency info
        stdout, _ = self.run_command("cat /proc/cpuinfo | grep MHz")
        if stdout:
            freqs = re.findall(r'cpu MHz\s*:\s*([\d.]+)', stdout)
            if freqs:
                cpu_info['current_freq'] = float(max(freqs))
        
        stdout, _ = self.run_command("cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq 2>/dev/null")
        if stdout:
            try:
                cpu_info['max_freq'] = float(stdout) / 1000  # Convert to MHz
            except:
                pass
        
        # Try to get temperature
        for sensor_path in ['/sys/class/hwmon/hwmon*/temp*_input', 
                           '/sys/class/thermal/thermal_zone*/temp']:
            stdout, _ = self.run_command(f"cat {sensor_path} 2>/dev/null | head -1")
            if stdout:
                try:
                    temp = float(stdout) / 1000 if 'thermal_zone' in sensor_path else float(stdout)
                    if 0 < temp < 150:  # Reasonable temperature range
                        cpu_info['temperature'] = temp
                        break
                except:
                    pass
        
        # Get CPU load
        stdout, _ = self.run_command("cat /proc/loadavg")
        if stdout:
            parts = stdout.split()
            if parts:
                cpu_info['load'] = float(parts[0])
        
        # Assess health
        issues = []
        if cpu_info['temperature']:
            if cpu_info['temperature'] > 90:
                issues.append("Critical: CPU temperature too high (>90°C)")
            elif cpu_info['temperature'] > 80:
                issues.append("Warning: CPU temperature elevated (>80°C)")
        
        if cpu_info['load'] and cpu_info['cores']:
            load_per_core = cpu_info['load'] / cpu_info['cores']
            if load_per_core > 2:
                issues.append("Warning: Very high CPU load")
            elif load_per_core > 1:
                issues.append("Notice: High CPU load")
        
        cpu_info['issues'] = issues
        cpu_info['health_status'] = 'critical' if any('Critical' in i for i in issues) else \
                                   'warning' if any('Warning' in i for i in issues) else 'good'
        
        return cpu_info
    
    def check_memory(self) -> Dict:
        """Check RAM specifications and health."""
        mem_info = {
            'total_gb': 0,
            'used_gb': 0,
            'available_gb': 0,
            'usage_percent': 0,
            'type': 'Unknown',
            'speed_mhz': None,
            'health_status': 'unknown',
            'issues': []
        }
        
        # Get memory info from /proc/meminfo
        stdout, _ = self.run_command("cat /proc/meminfo")
        if stdout:
            total_kb = re.search(r'MemTotal:\s*(\d+)', stdout)
            available_kb = re.search(r'MemAvailable:\s*(\d+)', stdout)
            free_kb = re.search(r'MemFree:\s*(\d+)', stdout)
            buffers_kb = re.search(r'Buffers:\s*(\d+)', stdout)
            cached_kb = re.search(r'^Cached:\s*(\d+)', stdout, re.MULTILINE)
            
            if total_kb:
                mem_info['total_gb'] = round(int(total_kb.group(1)) / 1024 / 1024, 2)
            
            if available_kb:
                mem_info['available_gb'] = round(int(available_kb.group(1)) / 1024 / 1024, 2)
                used = int(total_kb.group(1)) - int(available_kb.group(1))
                mem_info['used_gb'] = round(used / 1024 / 1024, 2)
                mem_info['usage_percent'] = round((used / int(total_kb.group(1))) * 100, 2)
            elif free_kb and buffers_kb and cached_kb:
                # Fallback calculation
                free_total = int(free_kb.group(1)) + int(buffers_kb.group(1)) + int(cached_kb.group(1))
                mem_info['available_gb'] = round(free_total / 1024 / 1024, 2)
        
        # Try to get RAM type and speed from dmidecode
        stdout, _ = self.run_command("sudo dmidecode -t memory 2>/dev/null | grep -E 'Type:|Speed:' | head -4")
        if stdout:
            types = re.findall(r'Type:\s*(\S+)', stdout)
            speeds = re.findall(r'Speed:\s*(\d+)', stdout)
            if types:
                mem_info['type'] = types[0]
            if speeds:
                mem_info['speed_mhz'] = int(speeds[0])
        
        # Check for memory errors in dmesg
        stdout, _ = self.run_command("dmesg 2>/dev/null | grep -i 'memory error\\|edac\\|ecc' | tail -5")
        if stdout:
            mem_info['issues'].append("Warning: Memory errors detected in system logs")
        
        # Assess health
        issues = []
        if mem_info['usage_percent'] > 95:
            issues.append("Critical: Memory usage critically high (>95%)")
        elif mem_info['usage_percent'] > 85:
            issues.append("Warning: Memory usage high (>85%)")
        
        if mem_info['total_gb'] < 2:
            issues.append("Notice: Very low total memory (<2GB)")
        elif mem_info['total_gb'] < 4:
            issues.append("Notice: Low total memory (<4GB)")
        
        mem_info['issues'] = issues + mem_info.get('issues', [])
        mem_info['health_status'] = 'critical' if any('Critical' in i for i in issues) else \
                                   'warning' if any('Warning' in i for i in issues) else 'good'
        
        return mem_info
    
    def check_storage(self) -> List[Dict]:
        """Check storage devices specifications and health."""
        storage_devices = []
        
        # Get list of block devices
        stdout, _ = self.run_command("lsblk -d -o NAME,MODEL,SIZE,TYPE 2>/dev/null | grep disk")
        if not stdout:
            return storage_devices
        
        lines = stdout.split('\n')
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                device = {
                    'name': f"/dev/{parts[0]}",
                    'model': ' '.join(parts[1:-2]) if len(parts) > 3 else 'Unknown',
                    'size': parts[-2],
                    'type': parts[-1],
                    'health_status': 'unknown',
                    'smart_status': 'unknown',
                    'temperature': None,
                    'power_on_hours': None,
                    'issues': []
                }
                
                # Try to get SMART data
                dev_name = parts[0]
                stdout, _ = self.run_command(f"sudo smartctl -H /dev/{dev_name} 2>/dev/null")
                if stdout:
                    if 'PASSED' in stdout:
                        device['smart_status'] = 'passed'
                    elif 'FAILED' in stdout:
                        device['smart_status'] = 'failed'
                        device['issues'].append("Critical: SMART test failed")
                
                # Get more detailed SMART info
                stdout, _ = self.run_command(f"sudo smartctl -A /dev/{dev_name} 2>/dev/null")
                if stdout:
                    # Temperature
                    temp_match = re.search(r'Temperature_Current.*?\s+(\d+)', stdout)
                    if temp_match:
                        device['temperature'] = int(temp_match.group(1))
                    
                    # Power on hours
                    poh_match = re.search(r'Power_On_Hours.*?\s+(\d+)', stdout)
                    if poh_match:
                        device['power_on_hours'] = int(poh_match.group(1))
                    
                    # Check for reallocated sectors
                    realloc_match = re.search(r'Reallocated_Sector_Ct.*?\s+(\d+)', stdout)
                    if realloc_match and int(realloc_match.group(1)) > 0:
                        count = int(realloc_match.group(1))
                        if count > 100:
                            device['issues'].append(f"Warning: {count} reallocated sectors")
                        else:
                            device['issues'].append(f"Notice: {count} reallocated sectors")
                    
                    # Check for pending sectors
                    pending_match = re.search(r'Current_Pending_Sector.*?\s+(\d+)', stdout)
                    if pending_match and int(pending_match.group(1)) > 0:
                        device['issues'].append(f"Warning: {pending_match.group(1)} pending sectors")
                
                # Assess health
                issues = device.get('issues', [])
                if device['smart_status'] == 'failed':
                    device['health_status'] = 'critical'
                elif any('Critical' in i for i in issues):
                    device['health_status'] = 'critical'
                elif any('Warning' in i for i in issues):
                    device['health_status'] = 'warning'
                else:
                    device['health_status'] = 'good'
                
                # Age assessment
                if device['power_on_hours']:
                    years = device['power_on_hours'] / 24 / 365
                    if years > 7:
                        device['issues'].append(f"Notice: Drive is old ({years:.1f} years)")
                
                storage_devices.append(device)
        
        return storage_devices
    
    def check_gpu(self) -> Dict:
        """Check GPU specifications and health."""
        gpu_info = {
            'name': 'Unknown',
            'vendor': 'Unknown',
            'memory_mb': None,
            'temperature': None,
            'utilization': None,
            'driver': 'Unknown',
            'health_status': 'unknown',
            'issues': []
        }
        
        # Try lspci for GPU detection
        stdout, _ = self.run_command("lspci | grep -i vga")
        if not stdout:
            stdout, _ = self.run_command("lspci | grep -i 3d")
        
        if stdout:
            gpu_info['name'] = stdout.split(': ')[-1] if ': ' in stdout else stdout
        
        # Try to get NVIDIA GPU info
        stdout, _ = self.run_command("nvidia-smi --query-gpu=name,memory.total,temperature.gpu,utilization.gpu --format=csv,noheader 2>/dev/null")
        if stdout:
            parts = stdout.split(', ')
            if len(parts) >= 4:
                gpu_info['name'] = parts[0]
                gpu_info['vendor'] = 'NVIDIA'
                try:
                    gpu_info['memory_mb'] = int(parts[1].replace(' MiB', ''))
                    gpu_info['temperature'] = int(parts[2].replace(' C', ''))
                    gpu_info['utilization'] = int(parts[3].replace(' %', ''))
                except:
                    pass
                
                stdout, _ = self.run_command("nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null")
                if stdout:
                    gpu_info['driver'] = stdout
        
        # Try AMD GPU info
        if gpu_info['vendor'] == 'Unknown':
            stdout, _ = self.run_command("rocm-smi --showproductname 2>/dev/null")
            if stdout:
                gpu_info['vendor'] = 'AMD'
        
        # Check for GPU errors in dmesg
        stdout, _ = self.run_command("dmesg 2>/dev/null | grep -i 'gpu\\|graphics\\|drm' | grep -i 'error\\|fault' | tail -3")
        if stdout:
            gpu_info['issues'].append("Warning: GPU errors detected in system logs")
        
        # Assess health
        issues = gpu_info.get('issues', [])
        if gpu_info['temperature'] and gpu_info['temperature'] > 90:
            issues.append("Critical: GPU temperature too high (>90°C)")
        elif gpu_info['temperature'] and gpu_info['temperature'] > 80:
            issues.append("Warning: GPU temperature elevated (>80°C)")
        
        gpu_info['issues'] = issues
        gpu_info['health_status'] = 'critical' if any('Critical' in i for i in issues) else \
                                   'warning' if any('Warning' in i for i in issues) else 'good'
        
        return gpu_info
    
    def check_battery(self) -> Optional[Dict]:
        """Check battery health (for laptops)."""
        battery_info = {
            'present': False,
            'status': 'Unknown',
            'capacity_percent': None,
            'design_capacity_mah': None,
            'current_capacity_mah': None,
            'voltage': None,
            'health_status': 'unknown',
            'issues': []
        }
        
        # Check if battery exists
        stdout, _ = self.run_command("cat /sys/class/power_supply/BAT*/present 2>/dev/null | head -1")
        if not stdout or stdout == '0':
            return None
        
        battery_info['present'] = True
        
        # Get battery status
        stdout, _ = self.run_command("cat /sys/class/power_supply/BAT*/status 2>/dev/null | head -1")
        if stdout:
            battery_info['status'] = stdout
        
        # Get capacity info
        stdout, _ = self.run_command("cat /sys/class/power_supply/BAT*/capacity 2>/dev/null | head -1")
        if stdout:
            try:
                battery_info['capacity_percent'] = int(stdout)
            except:
                pass
        
        stdout, _ = self.run_command("cat /sys/class/power_supply/BAT*/charge_full_design 2>/dev/null | head -1")
        if stdout:
            try:
                battery_info['design_capacity_mah'] = int(stdout)
            except:
                pass
        
        stdout, _ = self.run_command("cat /sys/class/power_supply/BAT*/charge_full 2>/dev/null | head -1")
        if stdout and battery_info['design_capacity_mah']:
            try:
                battery_info['current_capacity_mah'] = int(stdout)
                # Calculate health
                health = (battery_info['current_capacity_mah'] / battery_info['design_capacity_mah']) * 100
                if health < 50:
                    battery_info['issues'].append("Critical: Battery health very poor (<50%)")
                elif health < 70:
                    battery_info['issues'].append("Warning: Battery health degraded (<70%)")
                elif health < 80:
                    battery_info['issues'].append("Notice: Battery health slightly degraded (<80%)")
            except:
                pass
        
        stdout, _ = self.run_command("cat /sys/class/power_supply/BAT*/voltage_now 2>/dev/null | head -1")
        if stdout:
            try:
                battery_info['voltage'] = round(float(stdout) / 1000000, 2)
            except:
                pass
        
        # Assess health
        issues = battery_info.get('issues', [])
        if battery_info['capacity_percent'] and battery_info['capacity_percent'] < 10:
            issues.append("Critical: Battery level very low (<10%)")
        elif battery_info['capacity_percent'] and battery_info['capacity_percent'] < 20:
            issues.append("Warning: Battery level low (<20%)")
        
        battery_info['issues'] = issues
        battery_info['health_status'] = 'critical' if any('Critical' in i for i in issues) else \
                                       'warning' if any('Warning' in i for i in issues) else 'good'
        
        return battery_info
    
    def check_system_info(self) -> Dict:
        """Get general system information."""
        sys_info = {
            'hostname': 'Unknown',
            'kernel': 'Unknown',
            'os': 'Unknown',
            'uptime_days': 0,
            'boot_time': None
        }
        
        # Hostname
        stdout, _ = self.run_command("hostname")
        if stdout:
            sys_info['hostname'] = stdout
        
        # Kernel version
        stdout, _ = self.run_command("uname -r")
        if stdout:
            sys_info['kernel'] = stdout
        
        # OS info
        stdout, _ = self.run_command("cat /etc/os-release | grep PRETTY_NAME")
        if stdout:
            sys_info['os'] = stdout.split('=')[1].strip().strip('"')
        
        # Uptime
        stdout, _ = self.run_command("cat /proc/uptime")
        if stdout:
            try:
                uptime_seconds = float(stdout.split()[0])
                sys_info['uptime_days'] = round(uptime_seconds / 86400, 2)
                sys_info['boot_time'] = datetime.fromtimestamp(
                    datetime.now().timestamp() - uptime_seconds
                ).isoformat()
            except:
                pass
        
        return sys_info
    
    def calculate_component_score(self, component_data: Dict) -> int:
        """Calculate a score from 1-10 for a component."""
        score = 10
        
        # Deduct points for issues
        for issue in component_data.get('issues', []):
            if 'Critical' in issue:
                score -= 3
            elif 'Warning' in issue:
                score -= 2
            elif 'Notice' in issue:
                score -= 1
        
        # Additional deductions based on health status
        if component_data.get('health_status') == 'critical':
            score = min(score, 3)
        elif component_data.get('health_status') == 'warning':
            score = min(score, 6)
        
        return max(1, min(10, score))
    
    def calculate_overall_score(self) -> int:
        """Calculate overall system score from 1-10."""
        scores = self.results['scores']
        
        if not scores:
            return 0
        
        # Weight different components
        weights = {
            'cpu': 0.25,
            'memory': 0.20,
            'storage': 0.25,
            'gpu': 0.15,
            'battery': 0.15  # Only if present
        }
        
        weighted_sum = 0
        total_weight = 0
        
        for component, score_list in scores.items():
            if score_list:
                avg_score = sum(score_list) / len(score_list)
                weight = weights.get(component, 0.1)
                
                # For battery, only count if present
                if component == 'battery' and not score_list[0]:
                    continue
                
                weighted_sum += avg_score * weight
                total_weight += weight
        
        if total_weight == 0:
            return 0
        
        overall = round(weighted_sum / total_weight)
        return max(1, min(10, overall))
    
    def generate_report(self) -> str:
        """Generate a formatted report."""
        report = []
        report.append("=" * 70)
        report.append("COMPUTER HARDWARE HEALTH REPORT")
        report.append("=" * 70)
        report.append(f"Generated: {self.results['timestamp']}")
        report.append("")
        
        # System Info
        sys_info = self.results['components'].get('system', {})
        report.append("-" * 70)
        report.append("SYSTEM INFORMATION")
        report.append("-" * 70)
        report.append(f"Hostname: {sys_info.get('hostname', 'N/A')}")
        report.append(f"OS: {sys_info.get('os', 'N/A')}")
        report.append(f"Kernel: {sys_info.get('kernel', 'N/A')}")
        report.append(f"Uptime: {sys_info.get('uptime_days', 0)} days")
        report.append(f"Boot Time: {sys_info.get('boot_time', 'N/A')}")
        report.append("")
        
        # CPU
        cpu = self.results['components'].get('cpu', {})
        report.append("-" * 70)
        report.append("CPU")
        report.append("-" * 70)
        report.append(f"Model: {cpu.get('name', 'N/A')}")
        report.append(f"Cores: {cpu.get('cores', 'N/A')} | Threads: {cpu.get('threads', 'N/A')}")
        report.append(f"Frequency: {cpu.get('current_freq', 0):.0f} MHz / {cpu.get('max_freq', 0):.0f} MHz")
        if cpu.get('temperature'):
            report.append(f"Temperature: {cpu['temperature']:.1f}°C")
        if cpu.get('load'):
            report.append(f"Load Average: {cpu['load']:.2f}")
        report.append(f"Health Status: {cpu.get('health_status', 'unknown').upper()}")
        report.append(f"Component Score: {self.results['scores'].get('cpu', [0])[0]}/10")
        if cpu.get('issues'):
            report.append("Issues:")
            for issue in cpu['issues']:
                report.append(f"  • {issue}")
        report.append("")
        
        # Memory
        mem = self.results['components'].get('memory', {})
        report.append("-" * 70)
        report.append("MEMORY (RAM)")
        report.append("-" * 70)
        report.append(f"Total: {mem.get('total_gb', 0)} GB")
        report.append(f"Used: {mem.get('used_gb', 0)} GB ({mem.get('usage_percent', 0)}%)")
        report.append(f"Available: {mem.get('available_gb', 0)} GB")
        if mem.get('type'):
            report.append(f"Type: {mem['type']}")
        if mem.get('speed_mhz'):
            report.append(f"Speed: {mem['speed_mhz']} MHz")
        report.append(f"Health Status: {mem.get('health_status', 'unknown').upper()}")
        report.append(f"Component Score: {self.results['scores'].get('memory', [0])[0]}/10")
        if mem.get('issues'):
            report.append("Issues:")
            for issue in mem['issues']:
                report.append(f"  • {issue}")
        report.append("")
        
        # Storage
        storage = self.results['components'].get('storage', [])
        report.append("-" * 70)
        report.append("STORAGE DEVICES")
        report.append("-" * 70)
        if storage:
            for i, dev in enumerate(storage, 1):
                report.append(f"\nDevice {i}: {dev.get('name', 'N/A')}")
                report.append(f"  Model: {dev.get('model', 'N/A')}")
                report.append(f"  Size: {dev.get('size', 'N/A')}")
                if dev.get('temperature'):
                    report.append(f"  Temperature: {dev['temperature']}°C")
                if dev.get('power_on_hours'):
                    years = dev['power_on_hours'] / 24 / 365
                    report.append(f"  Power On Hours: {dev['power_on_hours']} ({years:.1f} years)")
                report.append(f"  SMART Status: {dev.get('smart_status', 'unknown').upper()}")
                report.append(f"  Health Status: {dev.get('health_status', 'unknown').upper()}")
                if dev.get('issues'):
                    report.append("  Issues:")
                    for issue in dev['issues']:
                        report.append(f"    • {issue}")
        else:
            report.append("No storage devices detected or unable to read information.")
        
        if self.results['scores'].get('storage'):
            avg_score = sum(self.results['scores']['storage']) / len(self.results['scores']['storage'])
            report.append(f"\nStorage Average Score: {avg_score:.1f}/10")
        report.append("")
        
        # GPU
        gpu = self.results['components'].get('gpu', {})
        report.append("-" * 70)
        report.append("GRAPHICS (GPU)")
        report.append("-" * 70)
        report.append(f"Name: {gpu.get('name', 'N/A')}")
        report.append(f"Vendor: {gpu.get('vendor', 'N/A')}")
        if gpu.get('memory_mb'):
            report.append(f"Memory: {gpu['memory_mb']} MB")
        if gpu.get('temperature'):
            report.append(f"Temperature: {gpu['temperature']}°C")
        if gpu.get('utilization') is not None:
            report.append(f"Utilization: {gpu['utilization']}%")
        report.append(f"Driver: {gpu.get('driver', 'N/A')}")
        report.append(f"Health Status: {gpu.get('health_status', 'unknown').upper()}")
        report.append(f"Component Score: {self.results['scores'].get('gpu', [0])[0]}/10")
        if gpu.get('issues'):
            report.append("Issues:")
            for issue in gpu['issues']:
                report.append(f"  • {issue}")
        report.append("")
        
        # Battery
        battery = self.results['components'].get('battery')
        if battery and battery.get('present'):
            report.append("-" * 70)
            report.append("BATTERY")
            report.append("-" * 70)
            report.append(f"Status: {battery.get('status', 'N/A')}")
            report.append(f"Charge Level: {battery.get('capacity_percent', 'N/A')}%")
            if battery.get('design_capacity_mah') and battery.get('current_capacity_mah'):
                health = (battery['current_capacity_mah'] / battery['design_capacity_mah']) * 100
                report.append(f"Design Capacity: {battery['design_capacity_mah']} mAh")
                report.append(f"Current Capacity: {battery['current_capacity_mah']} mAh")
                report.append(f"Battery Health: {health:.1f}%")
            if battery.get('voltage'):
                report.append(f"Voltage: {battery['voltage']} V")
            report.append(f"Health Status: {battery.get('health_status', 'unknown').upper()}")
            report.append(f"Component Score: {self.results['scores'].get('battery', [0])[0]}/10")
            if battery.get('issues'):
                report.append("Issues:")
                for issue in battery['issues']:
                    report.append(f"  • {issue}")
            report.append("")
        
        # Overall Problems Summary
        all_problems = []
        for component_name in ['cpu', 'memory', 'gpu', 'battery']:
            comp_data = self.results['components'].get(component_name, {})
            for issue in comp_data.get('issues', []):
                all_problems.append(f"{component_name.upper()}: {issue}")
        
        for storage_dev in self.results['components'].get('storage', []):
            for issue in storage_dev.get('issues', []):
                all_problems.append(f"STORAGE ({storage_dev.get('name', 'unknown')}): {issue}")
        
        if all_problems:
            report.append("-" * 70)
            report.append("ALL DETECTED PROBLEMS")
            report.append("-" * 70)
            for problem in all_problems:
                report.append(f"⚠ {problem}")
            report.append("")
        
        # Overall Score
        report.append("=" * 70)
        report.append("OVERALL SYSTEM RANKING")
        report.append("=" * 70)
        overall_score = self.results['overall_score']
        
        # Score interpretation
        if overall_score >= 9:
            rating = "PERFECT CONDITION"
            emoji = "💎"
        elif overall_score >= 8:
            rating = "EXCELLENT"
            emoji = "⭐"
        elif overall_score >= 7:
            rating = "VERY GOOD"
            emoji = "✓"
        elif overall_score >= 6:
            rating = "GOOD"
            emoji = "👍"
        elif overall_score >= 5:
            rating = "FAIR"
            emoji = "➖"
        elif overall_score >= 4:
            rating = "POOR"
            emoji = "⚠"
        elif overall_score >= 3:
            rating = "VERY POOR"
            emoji = "❗"
        else:
            rating = "CRITICAL"
            emoji = "🚨"
        
        report.append(f"\n  OVERALL SCORE: {overall_score}/10")
        report.append(f"  RATING: {emoji} {rating}")
        report.append("\nScore Breakdown:")
        for component, scores in self.results['scores'].items():
            if scores:
                avg = sum(scores) / len(scores)
                report.append(f"  {component.upper()}: {avg:.1f}/10")
        
        report.append("\n" + "=" * 70)
        report.append("END OF REPORT")
        report.append("=" * 70)
        
        return "\n".join(report)
    
    def run_full_check(self):
        """Run all hardware checks and generate results."""
        print("Starting comprehensive hardware health check...")
        print("-" * 50)
        
        # System info
        print("Checking system information...")
        self.results['components']['system'] = self.check_system_info()
        
        # CPU
        print("Checking CPU...")
        self.results['components']['cpu'] = self.check_cpu()
        cpu_score = self.calculate_component_score(self.results['components']['cpu'])
        self.results['scores']['cpu'] = [cpu_score]
        
        # Memory
        print("Checking memory...")
        self.results['components']['memory'] = self.check_memory()
        mem_score = self.calculate_component_score(self.results['components']['memory'])
        self.results['scores']['memory'] = [mem_score]
        
        # Storage
        print("Checking storage devices...")
        storage_devices = self.check_storage()
        self.results['components']['storage'] = storage_devices
        storage_scores = [self.calculate_component_score(dev) for dev in storage_devices]
        self.results['scores']['storage'] = storage_scores if storage_scores else [5]
        
        # GPU
        print("Checking GPU...")
        self.results['components']['gpu'] = self.check_gpu()
        gpu_score = self.calculate_component_score(self.results['components']['gpu'])
        self.results['scores']['gpu'] = [gpu_score]
        
        # Battery (optional)
        print("Checking battery (if present)...")
        battery = self.check_battery()
        if battery:
            self.results['components']['battery'] = battery
            bat_score = self.calculate_component_score(battery)
            self.results['scores']['battery'] = [bat_score]
        else:
            self.results['scores']['battery'] = [0]  # No battery
        
        # Calculate overall score
        print("Calculating overall score...")
        self.results['overall_score'] = self.calculate_overall_score()
        
        print("-" * 50)
        print("Hardware check complete!\n")
        
        # Generate and print report
        report = self.generate_report()
        print(report)
        
        return self.results


def main():
    """Main entry point."""
    checker = HardwareChecker()
    results = checker.run_full_check()
    
    # Optionally save results to JSON file
    with open('hardware_health_report.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print("\nDetailed results saved to: hardware_health_report.json")
    
    return results['overall_score']


if __name__ == "__main__":
    score = main()
    exit(0 if score >= 5 else 1)
