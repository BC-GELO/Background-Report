#!/usr/bin/env python3
"""
Computer Hardware Health Checker and Ranker (cross-platform)
Checks components for specs, runtime, health, and problem reports.
Ranks overall computer condition from 1 (very poor) to 10 (perfect).

Works on Windows, Linux, and macOS.

Requires:
    pip install psutil

Optional (improves accuracy, no setup needed if present):
    - nvidia-smi on PATH -> detailed NVIDIA GPU stats on ANY platform
    - Windows: uses built-in PowerShell (Get-CimInstance / Get-PhysicalDisk) - no extra installs
    - Linux: uses dmidecode/smartctl/lsblk if installed (sudo for some details)
    - torch with CUDA -> generates real GPU compute load for the stress test (already present
      on most ComfyUI/Stable Diffusion setups); without it, GPU stress falls back to
      monitoring-only mode.

Run modes:
    python hardware_health_checker.py                 -> interactive menu
    python hardware_health_checker.py --mode health
    python hardware_health_checker.py --mode stress --duration standard --yes
    python hardware_health_checker.py --mode both
"""

import argparse
import hashlib
import json
import multiprocessing
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from typing import Dict, List, Tuple, Optional

try:
    import psutil
except ImportError:
    raise SystemExit(
        "This tool requires the 'psutil' package.\n"
        "Install it with:  pip install psutil"
    )

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MAC = platform.system() == "Darwin"


class HardwareChecker:
    def __init__(self):
        self.results = {
            'timestamp': datetime.now().isoformat(),
            'components': {},
            'problems': [],
            'scores': {},
            'overall_score': 0
        }

    # ------------------------------------------------------------------
    # Command helpers
    # ------------------------------------------------------------------

    def run_command(self, args: List[str], timeout: int = 15) -> Tuple[str, int]:
        """Run a command given as a list of args (no shell). Safe on every OS."""
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            return result.stdout.strip(), result.returncode
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return "", -1

    def run_shell(self, command: str, timeout: int = 15) -> Tuple[str, int]:
        """Run a raw shell pipeline (grep/awk/redirection). Linux/macOS only."""
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return result.stdout.strip(), result.returncode
        except (subprocess.TimeoutExpired, OSError):
            return "", -1

    def run_powershell(self, command: str, timeout: int = 20):
        """Run a PowerShell command and parse its JSON output. Windows only."""
        full_cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command", f"{command} | ConvertTo-Json -Compress -Depth 4"
        ]
        try:
            result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
            out = result.stdout.strip()
            if not out:
                return None
            return json.loads(out)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
            return None

    # ------------------------------------------------------------------
    # System info
    # ------------------------------------------------------------------

    def check_system_info(self) -> Dict:
        """Get general system information."""
        sys_info = {
            'hostname': platform.node() or 'Unknown',
            'kernel': platform.version(),
            'os': f"{platform.system()} {platform.release()}",
            'uptime_days': 0,
            'boot_time': None
        }

        if IS_LINUX:
            stdout, _ = self.run_shell("cat /etc/os-release 2>/dev/null | grep PRETTY_NAME")
            if stdout and '=' in stdout:
                sys_info['os'] = stdout.split('=', 1)[1].strip().strip('"')
            stdout, _ = self.run_command(["uname", "-r"])
            if stdout:
                sys_info['kernel'] = stdout
        elif IS_WINDOWS:
            caption = self.run_powershell("(Get-CimInstance Win32_OperatingSystem).Caption")
            if caption:
                sys_info['os'] = str(caption).strip()
        elif IS_MAC:
            stdout, _ = self.run_command(["sw_vers", "-productVersion"])
            if stdout:
                sys_info['os'] = f"macOS {stdout}"

        try:
            boot_ts = psutil.boot_time()
            uptime_seconds = datetime.now().timestamp() - boot_ts
            sys_info['uptime_days'] = round(uptime_seconds / 86400, 2)
            sys_info['boot_time'] = datetime.fromtimestamp(boot_ts).isoformat()
        except Exception:
            pass

        return sys_info

    # ------------------------------------------------------------------
    # CPU
    # ------------------------------------------------------------------

    def _windows_cpu_name(self) -> Optional[str]:
        # Fast path: direct registry read, no subprocess needed
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            if name:
                return re.sub(r'\s+', ' ', name).strip()
        except Exception:
            pass
        # Fallback: PowerShell/WMI
        data = self.run_powershell("(Get-CimInstance Win32_Processor).Name")
        if data:
            return str(data).strip()
        return None

    def check_cpu(self) -> Dict:
        """Check CPU specifications and health."""
        cpu_info = {
            'name': 'Unknown', 'cores': 0, 'threads': 0,
            'current_freq': 0, 'max_freq': 0,
            'temperature': None, 'load': None,
            'health_status': 'unknown', 'issues': []
        }

        # Cores / threads - reliable on every OS via psutil
        try:
            cpu_info['threads'] = psutil.cpu_count(logical=True) or 0
            cpu_info['cores'] = psutil.cpu_count(logical=False) or cpu_info['threads']
        except Exception:
            pass

        # Frequency - cross-platform via psutil
        try:
            freq = psutil.cpu_freq()
            if freq:
                cpu_info['current_freq'] = round(freq.current or 0, 0)
                cpu_info['max_freq'] = round(freq.max or freq.current or 0, 0)
        except Exception:
            pass

        # CPU name
        if IS_LINUX:
            stdout, _ = self.run_shell("cat /proc/cpuinfo")
            if stdout:
                m = re.search(r'model name\s*:\s*(.+)', stdout)
                if m:
                    cpu_info['name'] = m.group(1).strip()
        elif IS_WINDOWS:
            name = self._windows_cpu_name()
            if name:
                cpu_info['name'] = name
        elif IS_MAC:
            stdout, _ = self.run_command(["sysctl", "-n", "machdep.cpu.brand_string"])
            if stdout:
                cpu_info['name'] = stdout

        # Load average - psutil emulates this on Windows too (since psutil 5.6.2)
        try:
            load1, _, _ = psutil.getloadavg()
            cpu_info['load'] = round(load1, 2)
        except Exception:
            try:
                pct = psutil.cpu_percent(interval=0.3)
                cpu_info['load'] = round(pct / 100 * (cpu_info['threads'] or 1), 2)
            except Exception:
                pass

        # Temperature - reliable on Linux; needs 3rd-party sensors on Windows
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for _, entries in temps.items():
                    for entry in entries:
                        if entry.current and 0 < entry.current < 130:
                            cpu_info['temperature'] = round(entry.current, 1)
                            break
                    if cpu_info['temperature']:
                        break
        except (AttributeError, Exception):
            pass

        if IS_WINDOWS and cpu_info['temperature'] is None:
            cpu_info['issues'].append(
                "Notice: CPU temperature not available - Windows needs a third-party tool "
                "like HWiNFO64 or LibreHardwareMonitor for sensor access"
            )

        # Health assessment
        issues = cpu_info['issues']
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

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    def check_memory(self) -> Dict:
        """Check RAM specifications and health."""
        mem_info = {
            'total_gb': 0, 'used_gb': 0, 'available_gb': 0, 'usage_percent': 0,
            'type': 'Unknown', 'speed_mhz': None, 'channels': 'Unknown',
            'slots_used': None, 'health_status': 'unknown', 'issues': []
        }

        try:
            vm = psutil.virtual_memory()
            mem_info['total_gb'] = round(vm.total / (1024 ** 3), 2)
            mem_info['available_gb'] = round(vm.available / (1024 ** 3), 2)
            mem_info['used_gb'] = round((vm.total - vm.available) / (1024 ** 3), 2)
            mem_info['usage_percent'] = round(vm.percent, 2)
        except Exception:
            pass

        if IS_WINDOWS:
            data = self.run_powershell(
                "Get-CimInstance Win32_PhysicalMemory | "
                "Select-Object Speed,SMBIOSMemoryType,DeviceLocator"
            )
            if data:
                modules = data if isinstance(data, list) else [data]
                ddr_map = {20: 'DDR', 21: 'DDR2', 24: 'DDR3', 26: 'DDR4', 34: 'DDR5'}
                speeds = [m['Speed'] for m in modules if m.get('Speed')]
                types_found = {ddr_map[m['SMBIOSMemoryType']]
                               for m in modules if m.get('SMBIOSMemoryType') in ddr_map}
                if speeds:
                    mem_info['speed_mhz'] = max(speeds)
                if types_found:
                    mem_info['type'] = '/'.join(sorted(types_found))
                mem_info['slots_used'] = len(modules)
            else:
                mem_info['issues'].append(
                    "Notice: Detailed RAM specs (type/speed) unavailable from this account/system"
                )
        elif IS_LINUX:
            stdout, retcode = self.run_shell("sudo -n dmidecode -t memory 2>/dev/null")
            if retcode == 0 and stdout:
                types = re.findall(r'Type:\s*(\S+)', stdout)
                known_types = [t for t in types if t not in ['Unknown', 'DDR']]
                if known_types:
                    mem_info['type'] = max(set(known_types), key=known_types.count)
                elif types:
                    mem_info['type'] = types[0]
                speeds = [int(s) for s in re.findall(r'Speed:\s*(\d+)', stdout)
                          if s.isdigit() and int(s) > 0]
                if speeds:
                    mem_info['speed_mhz'] = max(speeds)
                handles = re.findall(r'Handle:\s*0x\d+', stdout)
                if handles:
                    mem_info['slots_used'] = len(handles)
            else:
                mem_info['issues'].append(
                    "Notice: Detailed memory specs (type/speed) need passwordless 'sudo dmidecode' access"
                )

        # Health assessment (same thresholds as the original tool)
        issues = mem_info['issues']
        if mem_info['usage_percent'] > 95:
            issues.append("Critical: Memory usage critically high (>95%) - system may become unresponsive")
        elif mem_info['usage_percent'] > 85:
            issues.append("Warning: Memory usage high (>85%) - consider closing applications or adding RAM")
        elif mem_info['usage_percent'] > 70:
            issues.append("Notice: Memory usage moderately high (>70%)")

        if mem_info['total_gb'] < 2:
            issues.append("Critical: Very low total memory (<2GB) - system will struggle with modern applications")
        elif mem_info['total_gb'] < 4:
            issues.append("Warning: Low total memory (<4GB) - limited multitasking capability")
        elif mem_info['total_gb'] < 8:
            issues.append("Notice: Below average memory (<8GB) - may limit performance for demanding tasks")
        elif mem_info['total_gb'] >= 64:
            issues.append("Notice: High capacity memory (>=64GB) - excellent for demanding workloads")

        mem_info['issues'] = issues
        mem_info['health_status'] = 'critical' if any('Critical' in i for i in issues) else \
                                     'warning' if any('Warning' in i for i in issues) else 'good'
        return mem_info

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def check_storage(self) -> List[Dict]:
        """Check storage devices specifications and health."""
        if IS_WINDOWS:
            return self._check_storage_windows()
        elif IS_LINUX:
            return self._check_storage_linux()
        elif IS_MAC:
            return self._check_storage_mac()
        return []

    def _check_storage_windows(self) -> List[Dict]:
        storage_devices = []
        data = self.run_powershell(
            "Get-PhysicalDisk | Select-Object FriendlyName,SerialNumber,Size,MediaType,"
            "HealthStatus,OperationalStatus,BusType"
        )
        disk_list = data if isinstance(data, list) else ([data] if data else [])

        for d in disk_list:
            model = (d.get('FriendlyName') or 'Unknown').strip()
            size_bytes = d.get('Size') or 0
            size_gb = round(size_bytes / (1024 ** 3), 1) if size_bytes else 0
            status = (d.get('HealthStatus') or 'Unknown').strip()

            device = {
                'name': model, 'model': model,
                'serial': (d.get('SerialNumber') or 'N/A').strip(),
                'size': f"{size_gb}G" if size_gb else 'Unknown',
                'type': d.get('MediaType', 'disk'),
                'health_status': 'good', 'smart_status': 'unavailable',
                'temperature': None, 'power_on_hours': None, 'issues': []
            }

            if status == 'Healthy':
                device['smart_status'] = 'passed'
                device['health_status'] = 'good'
            elif status == 'Warning':
                device['smart_status'] = 'warning'
                device['health_status'] = 'warning'
                device['issues'].append("Warning: Drive reports degraded health status")
            elif status == 'Unhealthy':
                device['smart_status'] = 'failed'
                device['health_status'] = 'critical'
                device['issues'].append("Critical: Drive reports unhealthy status - back up data immediately")
            else:
                device['issues'].append("Notice: Health status unavailable - try running as Administrator")

            storage_devices.append(device)

        if not storage_devices:
            # Fallback if Get-PhysicalDisk (Storage module) isn't available
            data = self.run_powershell("Get-CimInstance Win32_DiskDrive | Select-Object Model,Size")
            disk_list = data if isinstance(data, list) else ([data] if data else [])
            for d in disk_list:
                model = (d.get('Model') or 'Unknown').strip()
                size_bytes = d.get('Size') or 0
                size_gb = round(size_bytes / (1024 ** 3), 1) if size_bytes else 0
                storage_devices.append({
                    'name': model, 'model': model, 'serial': 'N/A',
                    'size': f"{size_gb}G" if size_gb else 'Unknown', 'type': 'disk',
                    'health_status': 'unknown', 'smart_status': 'unavailable',
                    'temperature': None, 'power_on_hours': None,
                    'issues': ["Notice: Detailed health status unavailable on this system"]
                })

        return storage_devices

    def _check_storage_mac(self) -> List[Dict]:
        storage_devices = []
        stdout, _ = self.run_command(["diskutil", "list"])
        if stdout:
            storage_devices.append({
                'name': 'macOS disks', 'model': 'See diskutil info <disk>', 'serial': 'N/A',
                'size': 'Unknown', 'type': 'disk', 'health_status': 'unknown',
                'smart_status': 'unavailable', 'temperature': None, 'power_on_hours': None,
                'issues': ["Notice: Run 'diskutil info /dev/diskN' per device for SMART status"]
            })
        return storage_devices

    def _check_storage_linux(self) -> List[Dict]:
        """Original Linux deep-dive: lsblk + smartctl + sysfs."""
        storage_devices = []

        stdout, retcode = self.run_shell("lsblk -d -o NAME,MODEL,SIZE,TYPE 2>/dev/null | grep -E 'disk|lvm'")
        if not stdout or retcode != 0:
            stdout, retcode = self.run_shell("lsblk -d -o NAME,SIZE 2>/dev/null | grep -E 'disk|lvm'")
            if not stdout or retcode != 0:
                stdout, retcode = self.run_shell("cat /proc/partitions | tail -n +3 | awk '{print $4, $3}'")
                if not stdout or retcode != 0:
                    return storage_devices

        lines = stdout.split('\n')
        for line in lines:
            parts = line.split()
            if len(parts) < 2:
                continue
            dev_name = parts[0]
            if dev_name.startswith('ram') or dev_name.startswith('loop'):
                continue

            model = 'Unknown'
            serial = 'N/A'

            stdout_sysfs, _ = self.run_shell(f"cat /sys/block/{dev_name}/device/model 2>/dev/null")
            if stdout_sysfs:
                model = stdout_sysfs.strip()

            if model == 'Unknown' or not model:
                stdout_hdparm, _ = self.run_shell(f"hdparm -I /dev/{dev_name} 2>/dev/null | grep 'Model Number'")
                if stdout_hdparm:
                    model = stdout_hdparm.replace('Model Number:', '').strip()

            if model == 'Unknown' or not model:
                stdout_smart, _ = self.run_shell(f"smartctl -i /dev/{dev_name} 2>/dev/null | grep 'Device Model'")
                if stdout_smart:
                    model = stdout_smart.replace('Device Model:', '').strip()

            stdout_sysfs, _ = self.run_shell(f"cat /sys/block/{dev_name}/device/serial 2>/dev/null")
            if stdout_sysfs:
                serial = stdout_sysfs.strip()

            size = 'Unknown'
            if len(parts) >= 3:
                size_candidate = parts[-2]
                if any(c.isdigit() for c in size_candidate):
                    size = size_candidate
            if size == 'Unknown':
                stdout_sysfs, _ = self.run_shell(f"cat /sys/block/{dev_name}/size 2>/dev/null")
                if stdout_sysfs:
                    try:
                        sectors = int(stdout_sysfs.strip())
                        size_gb = sectors * 512 / 1024 / 1024 / 1024
                        size = f"{size_gb:.1f}G"
                    except ValueError:
                        pass

            dev_type = parts[-1] if parts[-1] in ['disk', 'lvm', 'dm'] else 'disk'

            device = {
                'name': f"/dev/{dev_name}", 'model': model, 'serial': serial, 'size': size,
                'type': dev_type, 'health_status': 'unknown', 'smart_status': 'unknown',
                'temperature': None, 'power_on_hours': None, 'issues': []
            }

            stdout, retcode = self.run_shell(f"sudo -n smartctl -H /dev/{dev_name} 2>/dev/null")
            if retcode == 0 and stdout:
                if 'PASSED' in stdout:
                    device['smart_status'] = 'passed'
                elif 'FAILED' in stdout:
                    device['smart_status'] = 'failed'
                    device['issues'].append("Critical: SMART test failed - drive may be failing!")
            else:
                device['smart_status'] = 'unavailable'
                device['issues'].append("Notice: SMART data unavailable (may need sudo or drive doesn't support it)")

            stdout, retcode = self.run_shell(f"sudo -n smartctl -A /dev/{dev_name} 2>/dev/null")
            if retcode == 0 and stdout:
                temp_match = re.search(r'Temperature_Current.*?\s+(\d+)', stdout) or \
                             re.search(r'194\s+Temperature_Celsius.*?\s+(\d+)', stdout)
                if temp_match:
                    device['temperature'] = int(temp_match.group(1))

                poh_match = re.search(r'Power_On_Hours.*?\s+(\d+)', stdout) or \
                            re.search(r'9\s+Power_On_Hours.*?\s+(\d+)', stdout)
                if poh_match:
                    device['power_on_hours'] = int(poh_match.group(1))

                realloc_match = re.search(r'Reallocated_Sector_Ct.*?\s+(\d+)', stdout) or \
                                 re.search(r'5\s+Reallocated_Sector_Ct.*?\s+(\d+)', stdout)
                if realloc_match and int(realloc_match.group(1)) > 0:
                    count = int(realloc_match.group(1))
                    if count > 100:
                        device['issues'].append(f"Warning: {count} reallocated sectors - monitor closely")
                    elif count > 10:
                        device['issues'].append(f"Notice: {count} reallocated sectors")
                    else:
                        device['issues'].append(f"Notice: {count} reallocated sector(s)")

                pending_match = re.search(r'Current_Pending_Sector.*?\s+(\d+)', stdout) or \
                                 re.search(r'197\s+Current_Pending_Sector.*?\s+(\d+)', stdout)
                if pending_match and int(pending_match.group(1)) > 0:
                    device['issues'].append(f"Warning: {pending_match.group(1)} pending sectors - possible bad sectors")

                uncorr_match = re.search(r'Offline_Uncorrectable.*?\s+(\d+)', stdout) or \
                               re.search(r'198\s+Offline_Uncorrectable.*?\s+(\d+)', stdout)
                if uncorr_match and int(uncorr_match.group(1)) > 0:
                    device['issues'].append(f"Warning: {uncorr_match.group(1)} uncorrectable errors")

            issues = device.get('issues', [])
            if device['smart_status'] == 'failed' or any('Critical' in i for i in issues):
                device['health_status'] = 'critical'
            elif any('Warning' in i for i in issues):
                device['health_status'] = 'warning'
            else:
                device['health_status'] = 'good'

            if device['power_on_hours']:
                years = device['power_on_hours'] / 24 / 365
                if years > 7:
                    device['issues'].append(f"Notice: Drive is old ({years:.1f} years) - consider backup")
                elif years > 5:
                    device['issues'].append(f"Notice: Drive has significant usage ({years:.1f} years)")

            storage_devices.append(device)

        return storage_devices

    # ------------------------------------------------------------------
    # GPU
    # ------------------------------------------------------------------

    def check_gpu(self) -> Dict:
        """Check GPU specifications and health."""
        gpu_info = {
            'name': 'No GPU detected or integrated graphics', 'vendor': 'Unknown',
            'memory_mb': None, 'temperature': None, 'utilization': None,
            'driver': 'Unknown', 'health_status': 'unknown', 'issues': []
        }

        def set_vendor_from_name():
            gpu_lower = gpu_info['name'].lower()
            if 'nvidia' in gpu_lower:
                gpu_info['vendor'] = 'NVIDIA'
            elif 'amd' in gpu_lower or 'ati' in gpu_lower or 'radeon' in gpu_lower:
                gpu_info['vendor'] = 'AMD'
            elif 'intel' in gpu_lower:
                gpu_info['vendor'] = 'Intel'

        if IS_WINDOWS:
            data = self.run_powershell(
                "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion"
            )
            gpus = data if isinstance(data, list) else ([data] if data else [])
            if gpus:
                names = [g.get('Name', '').strip() for g in gpus if g.get('Name')]
                if names:
                    gpu_info['name'] = '; '.join(names)
                    gpu_info['driver'] = gpus[0].get('DriverVersion', 'Unknown')
                    ram = gpus[0].get('AdapterRAM')
                    # AdapterRAM is a 32-bit field in WMI and is unreliable/overflows above ~4GB
                    # VRAM - only trust it in the safe range, prefer nvidia-smi otherwise.
                    if ram and 0 < ram < 4_000_000_000:
                        gpu_info['memory_mb'] = round(ram / (1024 ** 2))
                    set_vendor_from_name()

        elif IS_LINUX:
            stdout, retcode = self.run_shell("lspci 2>/dev/null | grep -iE 'vga|3d|display'")
            if stdout:
                gpu_devices = [line.split(': ')[-1].strip() for line in stdout.split('\n') if ': ' in line]
                if gpu_devices:
                    gpu_info['name'] = '; '.join(gpu_devices)
                    set_vendor_from_name()
                    if gpu_info['vendor'] == 'Unknown' and \
                            any(v in gpu_info['name'].lower() for v in ['vmware', 'qemu', 'virtual']):
                        gpu_info['vendor'] = 'Virtual'
                        gpu_info['issues'].append("Notice: Virtual/Emulated GPU detected")

        elif IS_MAC:
            stdout, _ = self.run_command(["system_profiler", "SPDisplaysDataType"])
            if stdout:
                m = re.search(r'Chipset Model:\s*(.+)', stdout)
                if m:
                    gpu_info['name'] = m.group(1).strip()
                    set_vendor_from_name()
                    if gpu_info['vendor'] == 'Unknown' and 'apple' in gpu_info['name'].lower():
                        gpu_info['vendor'] = 'Apple'

        # nvidia-smi behaves the same on every OS as long as the driver/tool is installed
        if shutil.which('nvidia-smi'):
            stdout, retcode = self.run_command([
                "nvidia-smi",
                "--query-gpu=name,memory.total,temperature.gpu,utilization.gpu,driver_version",
                "--format=csv,noheader,nounits"
            ])
            if retcode == 0 and stdout:
                lines = [l for l in stdout.split('\n') if l.strip()]
                names = []
                first_parts = None
                for line in lines:
                    parts = [p.strip() for p in line.split(',')]
                    if parts:
                        names.append(parts[0])
                        if first_parts is None:
                            first_parts = parts
                if names:
                    gpu_info['name'] = '; '.join(names)
                    gpu_info['vendor'] = 'NVIDIA'
                if first_parts and len(first_parts) >= 5:
                    try:
                        gpu_info['memory_mb'] = int(first_parts[1])
                        gpu_info['temperature'] = int(first_parts[2])
                        gpu_info['utilization'] = int(first_parts[3])
                        gpu_info['driver'] = first_parts[4]
                    except (ValueError, IndexError):
                        pass
        elif gpu_info['vendor'] == 'NVIDIA':
            gpu_info['issues'].append(
                "Warning: NVIDIA GPU detected but nvidia-smi isn't on PATH - "
                "driver may not be installed correctly"
            )

        if gpu_info['vendor'] == 'Unknown':
            gpu_info['issues'].append("Notice: No GPU detected - system may use basic display adapter")
            gpu_info['issues'].append("Notice: Graphics performance may be limited")

        issues = gpu_info['issues']
        if gpu_info['temperature']:
            if gpu_info['temperature'] > 90:
                issues.append("Critical: GPU temperature too high (>90°C) - immediate attention needed!")
            elif gpu_info['temperature'] > 80:
                issues.append("Warning: GPU temperature elevated (>80°C) - check cooling")
            elif gpu_info['temperature'] > 70:
                issues.append("Notice: GPU temperature moderately high (>70°C)")

        if gpu_info['utilization'] is not None and gpu_info['utilization'] > 95:
            issues.append("Notice: GPU utilization very high (>95%)")

        gpu_info['issues'] = issues
        gpu_info['health_status'] = 'critical' if any('Critical' in i for i in issues) else \
                                     'warning' if any('Warning' in i for i in issues) else 'good'
        return gpu_info

    # ------------------------------------------------------------------
    # Battery
    # ------------------------------------------------------------------

    def check_battery(self) -> Optional[Dict]:
        """Check battery health (for laptops). psutil handles presence/charge on all OSes."""
        try:
            batt = psutil.sensors_battery()
        except Exception:
            batt = None

        if not batt:
            return None

        battery_info = {
            'present': True,
            'status': 'Charging' if batt.power_plugged else 'Discharging',
            'capacity_percent': round(batt.percent, 1),
            'design_capacity_mah': None, 'current_capacity_mah': None,
            'voltage': None, 'health_status': 'unknown', 'issues': []
        }

        if IS_LINUX:
            stdout, _ = self.run_shell("cat /sys/class/power_supply/BAT*/charge_full_design 2>/dev/null | head -1")
            design = None
            if stdout:
                try:
                    design = int(stdout)
                    battery_info['design_capacity_mah'] = design
                except ValueError:
                    pass
            stdout, _ = self.run_shell("cat /sys/class/power_supply/BAT*/charge_full 2>/dev/null | head -1")
            if stdout and design:
                try:
                    current = int(stdout)
                    battery_info['current_capacity_mah'] = current
                    health = (current / design) * 100
                    if health < 50:
                        battery_info['issues'].append("Critical: Battery health very poor (<50%)")
                    elif health < 70:
                        battery_info['issues'].append("Warning: Battery health degraded (<70%)")
                    elif health < 80:
                        battery_info['issues'].append("Notice: Battery health slightly degraded (<80%)")
                except ValueError:
                    pass
        elif IS_WINDOWS:
            battery_info['issues'].append(
                "Notice: Battery wear level needs a generated report - run "
                "'powercfg /batteryreport' in an elevated prompt and open the HTML file"
            )

        issues = battery_info['issues']
        if battery_info['capacity_percent'] < 10:
            issues.append("Critical: Battery level very low (<10%)")
        elif battery_info['capacity_percent'] < 20:
            issues.append("Warning: Battery level low (<20%)")

        battery_info['issues'] = issues
        battery_info['health_status'] = 'critical' if any('Critical' in i for i in issues) else \
                                         'warning' if any('Warning' in i for i in issues) else 'good'
        return battery_info

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def calculate_component_score(self, component_data: Dict) -> int:
        """Calculate a score from 1-10 for a component."""
        score = 10
        for issue in component_data.get('issues', []):
            if 'Critical' in issue:
                score -= 3
            elif 'Warning' in issue:
                score -= 2
            elif 'Notice' in issue:
                score -= 1

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

        weights = {'cpu': 0.25, 'memory': 0.20, 'storage': 0.25, 'gpu': 0.15, 'battery': 0.15}
        weighted_sum = 0
        total_weight = 0

        for component, score_list in scores.items():
            if not score_list:
                continue
            if component == 'battery' and not score_list[0]:
                continue
            avg_score = sum(score_list) / len(score_list)
            weight = weights.get(component, 0.1)
            weighted_sum += avg_score * weight
            total_weight += weight

        if total_weight == 0:
            return 0
        return max(1, min(10, round(weighted_sum / total_weight)))

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def generate_report(self) -> str:
        """Generate a formatted report."""
        report = []
        report.append("=" * 70)
        report.append("COMPUTER HARDWARE HEALTH REPORT")
        report.append("=" * 70)
        report.append(f"Generated: {self.results['timestamp']}")
        report.append("")

        sys_info = self.results['components'].get('system', {})
        report.append("-" * 70)
        report.append("SYSTEM INFORMATION")
        report.append("-" * 70)
        report.append(f"Hostname: {sys_info.get('hostname', 'N/A')}")
        report.append(f"OS: {sys_info.get('os', 'N/A')}")
        report.append(f"Kernel/Build: {sys_info.get('kernel', 'N/A')}")
        report.append(f"Uptime: {sys_info.get('uptime_days', 0)} days")
        report.append(f"Boot Time: {sys_info.get('boot_time', 'N/A')}")
        report.append("")

        cpu = self.results['components'].get('cpu', {})
        report.append("-" * 70)
        report.append("CPU")
        report.append("-" * 70)
        report.append(f"Model: {cpu.get('name', 'N/A')}")
        report.append(f"Cores: {cpu.get('cores', 'N/A')} | Threads: {cpu.get('threads', 'N/A')}")
        report.append(f"Frequency: {cpu.get('current_freq', 0):.0f} MHz / {cpu.get('max_freq', 0):.0f} MHz")
        if cpu.get('temperature'):
            report.append(f"Temperature: {cpu['temperature']:.1f}°C")
        if cpu.get('load') is not None:
            report.append(f"Load Average: {cpu['load']:.2f}")
        report.append(f"Health Status: {cpu.get('health_status', 'unknown').upper()}")
        report.append(f"Component Score: {self.results['scores'].get('cpu', [0])[0]}/10")
        if cpu.get('issues'):
            report.append("Issues:")
            for issue in cpu['issues']:
                report.append(f"  • {issue}")
        report.append("")

        mem = self.results['components'].get('memory', {})
        report.append("-" * 70)
        report.append("MEMORY (RAM)")
        report.append("-" * 70)
        report.append(f"Total: {mem.get('total_gb', 0)} GB")
        report.append(f"Used: {mem.get('used_gb', 0)} GB ({mem.get('usage_percent', 0)}%)")
        report.append(f"Available: {mem.get('available_gb', 0)} GB")
        if mem.get('type') and mem['type'] != 'Unknown':
            report.append(f"Type: {mem['type']}")
        if mem.get('speed_mhz'):
            report.append(f"Speed: {mem['speed_mhz']} MHz")
        if mem.get('slots_used'):
            report.append(f"Modules Installed: {mem['slots_used']}")
        report.append(f"Health Status: {mem.get('health_status', 'unknown').upper()}")
        report.append(f"Component Score: {self.results['scores'].get('memory', [0])[0]}/10")
        if mem.get('issues'):
            report.append("Issues:")
            for issue in mem['issues']:
                report.append(f"  • {issue}")
        report.append("")

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
                report.append(f"[WARNING] {problem}")
            report.append("")

        report.append("=" * 70)
        report.append("OVERALL SYSTEM RANKING")
        report.append("=" * 70)
        overall_score = self.results['overall_score']

        if overall_score >= 9:
            rating = "PERFECT CONDITION"
        elif overall_score >= 8:
            rating = "EXCELLENT"
        elif overall_score >= 7:
            rating = "VERY GOOD"
        elif overall_score >= 6:
            rating = "GOOD"
        elif overall_score >= 5:
            rating = "FAIR"
        elif overall_score >= 4:
            rating = "POOR"
        elif overall_score >= 3:
            rating = "VERY POOR"
        else:
            rating = "CRITICAL"

        report.append(f"\n  OVERALL SCORE: {overall_score}/10")
        report.append(f"  RATING: {rating}")
        report.append("\nScore Breakdown:")
        for component, scores in self.results['scores'].items():
            if scores:
                avg = sum(scores) / len(scores)
                report.append(f"  {component.upper()}: {avg:.1f}/10")

        report.append("\n" + "=" * 70)
        report.append("END OF REPORT")
        report.append("=" * 70)

        return "\n".join(report)

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def run_full_check(self):
        """Run all hardware checks and generate results."""
        print(f"Starting comprehensive hardware health check ({platform.system()})...")
        print("-" * 50)

        print("Checking system information...")
        self.results['components']['system'] = self.check_system_info()

        print("Checking CPU...")
        self.results['components']['cpu'] = self.check_cpu()
        self.results['scores']['cpu'] = [self.calculate_component_score(self.results['components']['cpu'])]

        print("Checking memory...")
        self.results['components']['memory'] = self.check_memory()
        self.results['scores']['memory'] = [self.calculate_component_score(self.results['components']['memory'])]

        print("Checking storage devices...")
        storage_devices = self.check_storage()
        self.results['components']['storage'] = storage_devices
        storage_scores = [self.calculate_component_score(dev) for dev in storage_devices]
        self.results['scores']['storage'] = storage_scores if storage_scores else [5]

        print("Checking GPU...")
        self.results['components']['gpu'] = self.check_gpu()
        self.results['scores']['gpu'] = [self.calculate_component_score(self.results['components']['gpu'])]

        print("Checking battery (if present)...")
        battery = self.check_battery()
        if battery:
            self.results['components']['battery'] = battery
            self.results['scores']['battery'] = [self.calculate_component_score(battery)]
        else:
            self.results['scores']['battery'] = [0]

        print("Calculating overall score...")
        self.results['overall_score'] = self.calculate_overall_score()

        print("-" * 50)
        print("Hardware check complete!\n")

        report = self.generate_report()
        print(report)

        return self.results


DURATION_PRESETS = {
    'quick': 60,
    'standard': 300,
    'extended': 900,
}


def _nvidia_smi_sample() -> Optional[Dict]:
    """One-shot temp/utilization/memory sample. Works on any OS if nvidia-smi is on PATH."""
    if not shutil.which('nvidia-smi'):
        return None
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        parts = result.stdout.strip().split('\n')[0].split(', ')
        return {
            'temp_c': int(parts[0]),
            'util_percent': int(parts[1]),
            'mem_used_mb': int(parts[2]),
        }
    except Exception:
        return None


def _cpu_burn_worker(end_time: float):
    """Pure-stdlib CPU burn (SHA-256 loop). Runs in its own process per logical core."""
    data = os.urandom(4096)
    n = 0
    while time.time() < end_time:
        hashlib.sha256(data).digest()
        n += 1
    return n


class StressTester:
    """Sustained-load tests for CPU, memory, disk, and GPU to surface stability problems
    that a point-in-time snapshot can't catch (throttling, unstable RAM, slow/failing
    drives, GPU instability)."""

    def __init__(self):
        self._stop_flag = threading.Event()

    # -- monitoring -----------------------------------------------------

    def _sample_cpu_monitor(self, interval: float, samples: List[Dict]):
        while not self._stop_flag.is_set():
            entry = {}
            try:
                entry['load_percent'] = psutil.cpu_percent(interval=None)
            except Exception:
                entry['load_percent'] = None
            try:
                freq = psutil.cpu_freq()
                entry['freq_mhz'] = freq.current if freq else None
            except Exception:
                entry['freq_mhz'] = None
            entry['temp_c'] = None
            try:
                temps = psutil.sensors_temperatures()
                for _, entries_ in (temps or {}).items():
                    for e in entries_:
                        if e.current and 0 < e.current < 130:
                            entry['temp_c'] = e.current
                            break
                    if entry['temp_c']:
                        break
            except Exception:
                pass
            samples.append(entry)
            time.sleep(interval)

    # -- CPU --------------------------------------------------------------

    def stress_cpu(self, duration_seconds: int) -> Dict:
        n_workers = psutil.cpu_count(logical=True) or 1
        print(f"\n[CPU] Loading {n_workers} core(s) for {duration_seconds}s...")
        end_time = time.time() + duration_seconds

        samples: List[Dict] = []
        self._stop_flag.clear()
        monitor = threading.Thread(target=self._sample_cpu_monitor, args=(2, samples), daemon=True)
        monitor.start()

        baseline_freq = None
        try:
            f = psutil.cpu_freq()
            baseline_freq = (f.max or f.current) if f else None
        except Exception:
            pass

        procs = []
        try:
            for _ in range(n_workers):
                p = multiprocessing.Process(target=_cpu_burn_worker, args=(end_time,))
                p.start()
                procs.append(p)
            for p in procs:
                p.join(timeout=duration_seconds + 30)
        finally:
            self._stop_flag.set()
            monitor.join(timeout=5)
            for p in procs:
                if p.is_alive():
                    p.terminate()

        worker_errors = sum(1 for p in procs if p.exitcode not in (0,))

        loads = [s['load_percent'] for s in samples if s.get('load_percent') is not None]
        freqs = [s['freq_mhz'] for s in samples if s.get('freq_mhz') is not None]
        temps = [s['temp_c'] for s in samples if s.get('temp_c') is not None]

        result = {
            'workers': n_workers,
            'duration_seconds': duration_seconds,
            'avg_load_percent': round(sum(loads) / len(loads), 1) if loads else None,
            'max_load_percent': round(max(loads), 1) if loads else None,
            'min_freq_mhz': round(min(freqs), 0) if freqs else None,
            'max_freq_mhz': round(max(freqs), 0) if freqs else None,
            'baseline_max_freq_mhz': round(baseline_freq, 0) if baseline_freq else None,
            'max_temp_c': round(max(temps), 1) if temps else None,
            'worker_errors': worker_errors,
            'issues': []
        }

        if worker_errors:
            result['issues'].append(f"Critical: {worker_errors} CPU stress worker(s) crashed during the test")

        if result['max_temp_c']:
            if result['max_temp_c'] > 95:
                result['issues'].append(f"Critical: CPU hit {result['max_temp_c']}°C under load - check cooling immediately")
            elif result['max_temp_c'] > 85:
                result['issues'].append(f"Warning: CPU reached {result['max_temp_c']}°C under sustained load")
        elif IS_WINDOWS:
            result['issues'].append(
                "Notice: Couldn't read CPU temperature during the test - needs HWiNFO64 or "
                "LibreHardwareMonitor on Windows"
            )

        if baseline_freq and result['min_freq_mhz'] and result['min_freq_mhz'] < baseline_freq * 0.7:
            result['issues'].append(
                f"Warning: CPU frequency dropped to {result['min_freq_mhz']:.0f} MHz "
                f"(baseline max {baseline_freq:.0f} MHz) under load - possible thermal "
                f"throttling or power limiting"
            )

        if result['avg_load_percent'] is not None and result['avg_load_percent'] < 80:
            result['issues'].append(
                "Notice: Average load stayed below 80% - the test may have been throttled or interrupted"
            )

        result['health_status'] = 'critical' if any('Critical' in i for i in result['issues']) else \
                                   'warning' if any('Warning' in i for i in result['issues']) else 'good'
        return result

    # -- Memory -----------------------------------------------------------

    def stress_memory(self, duration_seconds: int) -> Dict:
        available = psutil.virtual_memory().available
        test_size = int(min(available * 0.5, 8 * 1024 ** 3))
        chunk = 64 * 1024 * 1024
        test_size -= test_size % chunk

        print(f"\n[MEMORY] Pattern-testing {test_size / 1024**3:.2f} GB of RAM for {duration_seconds}s...")

        if test_size < chunk:
            return {
                'tested_gb': 0,
                'issues': ["Notice: Not enough available memory to run a meaningful stress test"],
                'health_status': 'good'
            }

        n_chunks = test_size // chunk
        pattern_a = bytes([0xAA]) * chunk
        pattern_b = bytes([0x55]) * chunk

        try:
            buf = bytearray(test_size)
        except MemoryError:
            return {
                'tested_gb': round(test_size / 1024 ** 3, 2),
                'issues': ["Critical: MemoryError while allocating the test buffer - system is low on usable RAM"],
                'health_status': 'critical'
            }

        end_time = time.time() + duration_seconds
        cycles = 0
        errors = 0

        try:
            while time.time() < end_time:
                pat = pattern_a if cycles % 2 == 0 else pattern_b
                for i in range(n_chunks):
                    s = i * chunk
                    buf[s:s + chunk] = pat
                for i in range(n_chunks):
                    s = i * chunk
                    if bytes(buf[s:s + chunk]) != pat:
                        errors += 1
                cycles += 1
        finally:
            del buf

        result = {
            'tested_gb': round(test_size / 1024 ** 3, 2),
            'cycles_completed': cycles,
            'pattern_errors': errors,
            'issues': []
        }
        if errors:
            result['issues'].append(
                f"Critical: {errors} memory pattern mismatch(es) detected across {cycles} cycle(s) - "
                f"possible faulty RAM"
            )
        if cycles == 0:
            result['issues'].append("Notice: Test duration too short to complete a full pattern cycle")

        result['health_status'] = 'critical' if any('Critical' in i for i in result['issues']) else 'good'
        return result

    # -- Disk ---------------------------------------------------------------

    def stress_disk(self, duration_seconds: int) -> Dict:
        tmp_dir = tempfile.gettempdir()
        try:
            free = shutil.disk_usage(tmp_dir).free
        except Exception:
            free = 1024 ** 3

        file_size = int(min(free * 0.1, 2 * 1024 ** 3))
        chunk = 16 * 1024 * 1024
        file_size -= file_size % chunk
        file_size = max(file_size, chunk)

        print(f"\n[DISK] Write/read/verify cycles with a {file_size / 1024**2:.0f} MB file for {duration_seconds}s...")

        test_path = os.path.join(tmp_dir, f"hwcheck_stress_{os.getpid()}.tmp")
        write_speeds, read_speeds, errors = [], [], []
        end_time = time.time() + duration_seconds
        cycles = 0

        try:
            while time.time() < end_time and cycles < 50:
                data_block = os.urandom(chunk)
                write_checksum = hashlib.sha256()

                t0 = time.time()
                with open(test_path, 'wb') as f:
                    written = 0
                    while written < file_size:
                        f.write(data_block)
                        write_checksum.update(data_block)
                        written += chunk
                    f.flush()
                    os.fsync(f.fileno())
                t1 = time.time()
                write_speeds.append((file_size / (1024 ** 2)) / max(t1 - t0, 0.001))

                read_checksum = hashlib.sha256()
                t0 = time.time()
                with open(test_path, 'rb') as f:
                    while True:
                        block = f.read(chunk)
                        if not block:
                            break
                        read_checksum.update(block)
                t1 = time.time()
                read_speeds.append((file_size / (1024 ** 2)) / max(t1 - t0, 0.001))

                if read_checksum.hexdigest() != write_checksum.hexdigest():
                    errors.append(f"Cycle {cycles + 1}: data read back did not match data written")

                cycles += 1
        except OSError as e:
            errors.append(f"OS error during disk test: {e}")
        finally:
            if os.path.exists(test_path):
                try:
                    os.remove(test_path)
                except OSError:
                    pass

        result = {
            'tested_file_gb': round(file_size / 1024 ** 3, 3),
            'cycles_completed': cycles,
            'avg_write_mbps': round(sum(write_speeds) / len(write_speeds), 1) if write_speeds else None,
            'avg_read_mbps': round(sum(read_speeds) / len(read_speeds), 1) if read_speeds else None,
            'min_write_mbps': round(min(write_speeds), 1) if write_speeds else None,
            'min_read_mbps': round(min(read_speeds), 1) if read_speeds else None,
            'issues': []
        }

        for err in errors:
            if 'did not match' in err:
                result['issues'].append(f"Critical: {err} - possible disk corruption or a failing drive")
            else:
                result['issues'].append(f"Warning: {err}")

        if result['avg_write_mbps'] is not None and result['avg_write_mbps'] < 10:
            result['issues'].append(f"Warning: Sustained write speed very low ({result['avg_write_mbps']} MB/s)")
        if result['avg_read_mbps'] is not None and result['avg_read_mbps'] < 10:
            result['issues'].append(f"Warning: Sustained read speed very low ({result['avg_read_mbps']} MB/s)")
        if cycles == 0:
            result['issues'].append("Notice: Not enough free disk space or time to complete a write/read cycle")

        result['health_status'] = 'critical' if any('Critical' in i for i in result['issues']) else \
                                   'warning' if any('Warning' in i for i in result['issues']) else 'good'
        return result

    # -- GPU ------------------------------------------------------------------

    def stress_gpu(self, duration_seconds: int) -> Dict:
        has_nvidia_smi = shutil.which('nvidia-smi') is not None
        torch_mod = None
        torch_cuda = False
        try:
            import torch as torch_mod  # noqa: local import, optional dependency
            torch_cuda = torch_mod.cuda.is_available()
        except ImportError:
            pass

        print(f"\n[GPU] {'Generating CUDA compute load' if torch_cuda else 'Monitoring only (no compute load)'} "
              f"for {duration_seconds if torch_cuda else min(duration_seconds, 10)}s...")

        samples: List[Dict] = []
        stop_evt = threading.Event()

        def monitor_loop():
            while not stop_evt.is_set():
                s = _nvidia_smi_sample()
                if s:
                    samples.append(s)
                time.sleep(2)

        mon_thread = None
        if has_nvidia_smi:
            mon_thread = threading.Thread(target=monitor_loop, daemon=True)
            mon_thread.start()

        compute_error = None
        if torch_cuda:
            try:
                device = torch_mod.device('cuda')
                a = torch_mod.randn((4096, 4096), device=device)
                b = torch_mod.randn((4096, 4096), device=device)
                end_time = time.time() + duration_seconds
                while time.time() < end_time:
                    _ = a @ b
                    torch_mod.cuda.synchronize()
            except Exception as e:
                compute_error = str(e)
        else:
            # No compute backend available - keep a short monitoring window only
            time.sleep(min(duration_seconds, 10))

        stop_evt.set()
        if mon_thread:
            mon_thread.join(timeout=5)

        result = {
            'compute_load_generated': torch_cuda and not compute_error,
            'samples_collected': len(samples),
            'issues': []
        }

        if not has_nvidia_smi:
            result['issues'].append(
                "Notice: nvidia-smi not found - can't monitor GPU temp/utilization "
                "(non-NVIDIA GPU, or driver tools not on PATH)"
            )
        if not torch_cuda:
            result['issues'].append(
                "Notice: No CUDA-capable PyTorch found, so no real compute load was generated - "
                "install torch in your AI workstation's environment for a true GPU stress test, "
                "or use a dedicated tool like FurMark (Windows) / gpu-burn (Linux)"
            )
        if compute_error:
            result['issues'].append(f"Critical: GPU compute load failed mid-test - {compute_error}")

        if samples:
            temps = [s['temp_c'] for s in samples if s.get('temp_c') is not None]
            utils = [s['util_percent'] for s in samples if s.get('util_percent') is not None]
            if temps:
                result['max_temp_c'] = max(temps)
                if result['max_temp_c'] > 90:
                    result['issues'].append(f"Critical: GPU hit {result['max_temp_c']}°C under load")
                elif result['max_temp_c'] > 83:
                    result['issues'].append(f"Warning: GPU reached {result['max_temp_c']}°C under load")
            if utils:
                result['avg_utilization_percent'] = round(sum(utils) / len(utils), 1)
                if torch_cuda and not compute_error and result['avg_utilization_percent'] < 50:
                    result['issues'].append(
                        "Notice: GPU utilization stayed low during the compute test - load may not have been effective"
                    )

        result['health_status'] = 'critical' if any('Critical' in i for i in result['issues']) else \
                                   'warning' if any('Warning' in i for i in result['issues']) else 'good'
        return result


def run_full_stress(duration_seconds: int, duration_tier: str) -> Dict:
    """Run CPU, memory, disk, and GPU stress tests back to back."""
    results = {
        'timestamp': datetime.now().isoformat(),
        'duration_tier': duration_tier,
        'duration_seconds': duration_seconds,
        'tests': {}
    }
    tester = StressTester()
    results['tests']['cpu'] = tester.stress_cpu(duration_seconds)
    results['tests']['memory'] = tester.stress_memory(duration_seconds)
    results['tests']['disk'] = tester.stress_disk(duration_seconds)
    results['tests']['gpu'] = tester.stress_gpu(duration_seconds)
    return results


def generate_stress_report(stress_results: Dict) -> str:
    """Format the stress test results as a readable report."""
    report = []
    report.append("=" * 70)
    report.append("HARDWARE STRESS TEST REPORT")
    report.append("=" * 70)
    report.append(f"Generated: {stress_results['timestamp']}")
    report.append(f"Duration tier: {stress_results['duration_tier']} "
                   f"({stress_results['duration_seconds']}s per component)")
    report.append("")

    tests = stress_results['tests']

    cpu = tests.get('cpu', {})
    report.append("-" * 70)
    report.append("CPU STRESS TEST")
    report.append("-" * 70)
    if cpu:
        report.append(f"Workers: {cpu.get('workers', 'N/A')}")
        report.append(f"Avg Load: {cpu.get('avg_load_percent', 'N/A')}% | Max Load: {cpu.get('max_load_percent', 'N/A')}%")
        if cpu.get('max_freq_mhz'):
            report.append(
                f"Frequency under load: {cpu.get('min_freq_mhz', 'N/A')}-{cpu.get('max_freq_mhz', 'N/A')} MHz "
                f"(baseline max: {cpu.get('baseline_max_freq_mhz', 'N/A')} MHz)"
            )
        if cpu.get('max_temp_c'):
            report.append(f"Max Temperature: {cpu['max_temp_c']}°C")
        report.append(f"Health Status: {cpu.get('health_status', 'unknown').upper()}")
        if cpu.get('issues'):
            report.append("Issues:")
            for issue in cpu['issues']:
                report.append(f"  • {issue}")
    report.append("")

    mem = tests.get('memory', {})
    report.append("-" * 70)
    report.append("MEMORY STRESS TEST")
    report.append("-" * 70)
    if mem:
        report.append(f"Tested: {mem.get('tested_gb', 'N/A')} GB | Cycles completed: {mem.get('cycles_completed', 'N/A')}")
        report.append(f"Pattern Errors: {mem.get('pattern_errors', 0)}")
        report.append(f"Health Status: {mem.get('health_status', 'unknown').upper()}")
        if mem.get('issues'):
            report.append("Issues:")
            for issue in mem['issues']:
                report.append(f"  • {issue}")
    report.append("")

    disk = tests.get('disk', {})
    report.append("-" * 70)
    report.append("DISK STRESS TEST")
    report.append("-" * 70)
    if disk:
        report.append(f"Test file size: {disk.get('tested_file_gb', 'N/A')} GB | Cycles completed: {disk.get('cycles_completed', 'N/A')}")
        report.append(f"Avg Write: {disk.get('avg_write_mbps', 'N/A')} MB/s | Avg Read: {disk.get('avg_read_mbps', 'N/A')} MB/s")
        report.append(f"Health Status: {disk.get('health_status', 'unknown').upper()}")
        if disk.get('issues'):
            report.append("Issues:")
            for issue in disk['issues']:
                report.append(f"  • {issue}")
    report.append("")

    gpu = tests.get('gpu', {})
    report.append("-" * 70)
    report.append("GPU STRESS TEST")
    report.append("-" * 70)
    if gpu:
        report.append(f"Compute load generated: {'Yes' if gpu.get('compute_load_generated') else 'No (monitoring only)'}")
        if gpu.get('max_temp_c'):
            report.append(f"Max Temperature: {gpu['max_temp_c']}°C")
        if gpu.get('avg_utilization_percent') is not None:
            report.append(f"Avg Utilization: {gpu['avg_utilization_percent']}%")
        report.append(f"Health Status: {gpu.get('health_status', 'unknown').upper()}")
        if gpu.get('issues'):
            report.append("Issues:")
            for issue in gpu['issues']:
                report.append(f"  • {issue}")
    report.append("")

    all_critical, all_warning = [], []
    for name, t in tests.items():
        for issue in t.get('issues', []):
            if 'Critical' in issue:
                all_critical.append(f"{name.upper()}: {issue}")
            elif 'Warning' in issue:
                all_warning.append(f"{name.upper()}: {issue}")

    report.append("=" * 70)
    report.append("SUMMARY")
    report.append("=" * 70)
    if not all_critical and not all_warning:
        report.append("No problems detected under sustained load. System looks stable.")
    else:
        if all_critical:
            report.append(f"\n{len(all_critical)} CRITICAL issue(s) found:")
            for i in all_critical:
                report.append(f"  [CRITICAL] {i}")
        if all_warning:
            report.append(f"\n{len(all_warning)} WARNING(s) found:")
            for i in all_warning:
                report.append(f"  [WARNING] {i}")
    report.append("\n" + "=" * 70)

    return "\n".join(report)


def prompt_duration() -> Tuple[str, int]:
    print("\nSelect stress test duration (applies to each component - CPU, memory, disk, GPU):")
    print("  1) Quick     (~1 min each, ~4 min total)")
    print("  2) Standard  (~5 min each, ~20 min total)")
    print("  3) Extended  (~15 min each, ~60 min total)")
    choices = {'1': 'quick', '2': 'standard', '3': 'extended'}
    while True:
        choice = input("Enter 1, 2, or 3 [default: 2]: ").strip() or '2'
        if choice in choices:
            tier = choices[choice]
            return tier, DURATION_PRESETS[tier]
        print("Please enter 1, 2, or 3.")


def confirm_stress_run() -> bool:
    print("\nThis will fully load all CPU cores, allocate a large block of RAM, write a large "
          "temporary file to disk, and (if supported) load the GPU.")
    print("Make sure your system has adequate cooling. Press Ctrl+C at any time to stop early.")
    answer = input("Continue? [y/N]: ").strip().lower()
    return answer in ('y', 'yes')


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Hardware health check and stress test")
    parser.add_argument('--mode', choices=['health', 'stress', 'both'],
                         help="Skip the interactive menu and run this mode directly")
    parser.add_argument('--duration', choices=['quick', 'standard', 'extended'],
                         help="Stress test duration tier (skips the interactive prompt)")
    parser.add_argument('--yes', action='store_true',
                         help="Skip the stress test confirmation prompt")
    args = parser.parse_args()

    mode = args.mode
    if not mode:
        print("=" * 70)
        print("HARDWARE HEALTH CHECKER & STRESS TESTER")
        print("=" * 70)
        print("1) Hardware Health Check (point-in-time snapshot)")
        print("2) Stress Test (sustained load - finds problems that only show up over time)")
        print("3) Both")
        choice = input("Choose an option [1/2/3, default 1]: ").strip() or '1'
        mode = {'1': 'health', '2': 'stress', '3': 'both'}.get(choice, 'health')

    overall_exit_score = 10

    if mode in ('health', 'both'):
        checker = HardwareChecker()
        results = checker.run_full_check()
        with open('hardware_health_report.json', 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print("\nDetailed results saved to: hardware_health_report.json")
        overall_exit_score = min(overall_exit_score, results['overall_score'])

    if mode in ('stress', 'both'):
        if args.duration:
            tier, tier_seconds = args.duration, DURATION_PRESETS[args.duration]
        else:
            tier, tier_seconds = prompt_duration()

        if args.yes or confirm_stress_run():
            try:
                stress_results = run_full_stress(tier_seconds, tier)
            except KeyboardInterrupt:
                print("\n\nStress test interrupted by user. Partial results were not saved.")
                return overall_exit_score
            with open('stress_test_report.json', 'w') as f:
                json.dump(stress_results, f, indent=2, default=str)
            print("\n" + generate_stress_report(stress_results))
            print("\nDetailed results saved to: stress_test_report.json")
            any_critical = any(
                'Critical' in i for t in stress_results['tests'].values() for i in t.get('issues', [])
            )
            if any_critical:
                overall_exit_score = min(overall_exit_score, 1)
        else:
            print("Stress test cancelled.")

    return overall_exit_score


if __name__ == "__main__":
    multiprocessing.freeze_support()
    score = main()
    exit(0 if score >= 5 else 1)