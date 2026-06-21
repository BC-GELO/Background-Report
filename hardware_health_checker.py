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
"""

import subprocess
import re
import json
import platform
import shutil
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


def main():
    """Main entry point."""
    checker = HardwareChecker()
    results = checker.run_full_check()

    with open('hardware_health_report.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print("\nDetailed results saved to: hardware_health_report.json")
    return results['overall_score']


if __name__ == "__main__":
    score = main()
    exit(0 if score >= 5 else 1)