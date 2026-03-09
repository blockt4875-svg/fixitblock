"""
FixItBlock — Hardware Profiler
Auto-detects and profiles the Proxmox host hardware to optimize AI recommendations
and adapt agent behavior to the environment.
"""

import os
import re
import json
import logging
import threading
from typing import Optional
from datetime import datetime, timezone

log = logging.getLogger("fixitblock.hardware")


class HardwareProfiler:
    """
    Detects everything about the underlying hardware and environment.
    Results are cached and refreshed periodically.
    Results feed directly into AI context for hardware-optimized recommendations.
    """

    def __init__(self, ssh_client=None):
        self.ssh       = ssh_client
        self._profile  = {}
        self._lock     = threading.Lock()
        self._profiled = False

    def profile(self) -> dict:
        """Run full hardware detection. Returns complete profile dict."""
        log.info("Hardware: starting full profile scan")
        p = {}

        p.update(self._detect_cpu())
        p.update(self._detect_memory())
        p.update(self._detect_storage())
        p.update(self._detect_network())
        p.update(self._detect_gpu())
        p.update(self._detect_proxmox())
        p.update(self._detect_virtualization())
        p.update(self._detect_zfs())
        p.update(self._detect_ceph())
        p.update(self._detect_os())
        p.update(self._detect_performance())

        p["profiled_at"] = datetime.now(timezone.utc).isoformat()
        p["profile_version"] = 2

        with self._lock:
            self._profile  = p
            self._profiled = True

        log.info(f"Hardware: profile complete — "
                 f"{p.get('cpu_cores',0)} cores, "
                 f"{p.get('ram_total_gb',0):.1f}GB RAM, "
                 f"{len(p.get('storage_devices',[]))} storage devices")
        return p

    def get(self) -> dict:
        with self._lock:
            return dict(self._profile)

    # ── CPU ──────────────────────────────────────────────────

    def _detect_cpu(self) -> dict:
        result = {
            "cpu_model":   "unknown",
            "cpu_cores":   0,
            "cpu_threads": 0,
            "cpu_sockets": 0,
            "cpu_mhz":     0,
            "cpu_flags":   [],
            "arch":        "unknown",
            "numa_nodes":  0,
        }
        try:
            rc, out, _ = self._run("cat /proc/cpuinfo")
            if rc == 0 and out:
                lines = out.splitlines()
                models   = [l.split(":",1)[1].strip() for l in lines if l.startswith("model name")]
                cores    = [l.split(":",1)[1].strip() for l in lines if l.startswith("cpu cores")]
                threads  = [l.split(":",1)[1].strip() for l in lines if l.startswith("siblings")]
                sockets  = len(set(l.split(":",1)[1].strip() for l in lines if l.startswith("physical id")))
                flags    = [l.split(":",1)[1].strip().split() for l in lines if l.startswith("flags")]
                mhz_list = [l.split(":",1)[1].strip() for l in lines if "cpu MHz" in l]

                if models:  result["cpu_model"]   = models[0]
                if cores:   result["cpu_cores"]   = int(cores[0])
                if threads: result["cpu_threads"] = int(threads[0])
                if sockets: result["cpu_sockets"] = sockets
                if flags:   result["cpu_flags"]   = flags[0][:20]  # key flags
                if mhz_list:result["cpu_mhz"]     = float(mhz_list[0])

            # Architecture
            rc2, arch, _ = self._run("uname -m")
            if rc2 == 0: result["arch"] = arch.strip()

            # NUMA nodes
            rc3, numa, _ = self._run("ls /sys/devices/system/node/ 2>/dev/null | grep -c node")
            if rc3 == 0 and numa.strip().isdigit():
                result["numa_nodes"] = int(numa.strip())

            # CPU features for optimization hints
            result["has_aes"]  = "aes"   in result["cpu_flags"]
            result["has_avx"]  = "avx"   in result["cpu_flags"]
            result["has_avx2"] = "avx2"  in result["cpu_flags"]
            result["has_nx"]   = "nx"    in result["cpu_flags"]
            result["has_vmx"]  = "vmx"   in result["cpu_flags"]  # Intel VT-x
            result["has_svm"]  = "svm"   in result["cpu_flags"]  # AMD-V
            result["is_vm_capable"] = result["has_vmx"] or result["has_svm"]

        except Exception as e:
            log.warning(f"CPU detection failed: {e}")
        return result

    # ── Memory ───────────────────────────────────────────────

    def _detect_memory(self) -> dict:
        result = {
            "ram_total_gb":   0,
            "ram_free_gb":    0,
            "ram_used_gb":    0,
            "ram_cached_gb":  0,
            "swap_total_gb":  0,
            "swap_used_gb":   0,
            "hugepages":      False,
            "hugepage_size":  0,
        }
        try:
            rc, out, _ = self._run("cat /proc/meminfo")
            if rc == 0:
                mem = {}
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        key = parts[0].rstrip(":")
                        val = int(parts[1]) if parts[1].isdigit() else 0
                        mem[key] = val

                result["ram_total_gb"]  = mem.get("MemTotal",   0) / 1e6
                result["ram_free_gb"]   = mem.get("MemAvailable",0) / 1e6
                result["ram_used_gb"]   = result["ram_total_gb"] - result["ram_free_gb"]
                result["ram_cached_gb"] = mem.get("Cached",     0) / 1e6
                result["swap_total_gb"] = mem.get("SwapTotal",  0) / 1e6
                result["swap_used_gb"]  = (mem.get("SwapTotal",0) - mem.get("SwapFree",0)) / 1e6
                result["hugepages"]     = mem.get("HugePages_Total",0) > 0
                result["hugepage_size"] = mem.get("Hugepagesize",0)

            # Memory channels / DIMM info (requires root)
            rc2, dimm, _ = self._run("dmidecode -t memory 2>/dev/null | grep -E 'Size|Speed|Type:' | head -20")
            if rc2 == 0 and dimm:
                result["dimm_info"] = dimm.strip()

        except Exception as e:
            log.warning(f"Memory detection failed: {e}")
        return result

    # ── Storage ──────────────────────────────────────────────

    def _detect_storage(self) -> dict:
        result = {
            "storage_devices": [],
            "storage_details": [],
            "nvme_devices":    [],
            "ssd_devices":     [],
            "hdd_devices":     [],
            "total_storage_tb": 0,
            "raid_detected":   False,
            "lvm_vgs":         [],
        }
        try:
            # Block devices
            rc, out, _ = self._run("lsblk -d -o NAME,SIZE,TYPE,ROTA,TRAN 2>/dev/null | tail -n +2")
            if rc == 0:
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 3:
                        name, size, dtype = parts[0], parts[1], parts[2]
                        rota  = parts[3] if len(parts) > 3 else "1"
                        tran  = parts[4] if len(parts) > 4 else ""
                        dev   = f"/dev/{name}"
                        result["storage_devices"].append(dev)
                        info  = {"dev": dev, "size": size, "type": dtype, "transport": tran}

                        if "nvme" in name.lower() or tran == "nvme":
                            result["nvme_devices"].append(dev)
                            info["category"] = "nvme"
                        elif rota == "0":
                            result["ssd_devices"].append(dev)
                            info["category"] = "ssd"
                        else:
                            result["hdd_devices"].append(dev)
                            info["category"] = "hdd"

                        result["storage_details"].append(info)

            # LVM volume groups
            rc2, vgs, _ = self._run("vgs --noheadings -o vg_name 2>/dev/null")
            if rc2 == 0 and vgs:
                result["lvm_vgs"] = [v.strip() for v in vgs.splitlines() if v.strip()]

            # RAID
            rc3, raid, _ = self._run("cat /proc/mdstat 2>/dev/null")
            if rc3 == 0 and "md" in (raid or ""):
                result["raid_detected"] = True
                result["raid_info"]     = raid.strip()

            # SMART status for key drives
            for dev in result["storage_devices"][:4]:
                rc4, smart, _ = self._run(f"smartctl -H {dev} 2>/dev/null | grep 'SMART overall'")
                if rc4 == 0 and smart:
                    result.setdefault("smart_status", {})[dev] = "PASSED" in smart

        except Exception as e:
            log.warning(f"Storage detection failed: {e}")
        return result

    # ── Network ──────────────────────────────────────────────

    def _detect_network(self) -> dict:
        result = {
            "network_interfaces": [],
            "bridge_interfaces":  [],
            "bond_interfaces":    [],
            "primary_ip":         "",
            "hostname":           "",
        }
        try:
            rc, out, _ = self._run("ip -j addr show 2>/dev/null || ip addr show")
            if rc == 0 and out:
                try:
                    interfaces = json.loads(out)
                    for iface in interfaces:
                        name  = iface.get("ifname","")
                        flags = iface.get("flags",[])
                        addrs = [a["local"] for a in iface.get("addr_info",[]) if a.get("family") == "inet"]
                        info  = {"name": name, "ips": addrs, "state": iface.get("operstate","")}
                        result["network_interfaces"].append(info)
                        if name.startswith("vmbr"):
                            result["bridge_interfaces"].append(name)
                        elif name.startswith("bond"):
                            result["bond_interfaces"].append(name)
                        if addrs and not result["primary_ip"]:
                            result["primary_ip"] = addrs[0]
                except json.JSONDecodeError:
                    pass

            rc2, host, _ = self._run("hostname -f 2>/dev/null || hostname")
            if rc2 == 0: result["hostname"] = host.strip()

            # Network card speeds
            rc3, speeds, _ = self._run("ethtool $(ip route | awk '/default/{print $5;exit}') 2>/dev/null | grep Speed")
            if rc3 == 0 and speeds:
                result["nic_speed"] = speeds.strip()

        except Exception as e:
            log.warning(f"Network detection failed: {e}")
        return result

    # ── GPU ──────────────────────────────────────────────────

    def _detect_gpu(self) -> dict:
        result = {"gpus": [], "gpu_memory_gb": 0, "cuda_available": False, "opencl_available": False}
        try:
            # lspci for GPU detection
            rc, out, _ = self._run("lspci 2>/dev/null | grep -Ei 'VGA|3D|display|GPU'")
            if rc == 0 and out:
                result["gpus"] = [line.split(":",2)[-1].strip() for line in out.splitlines()]

            # NVIDIA
            rc2, nvidia, _ = self._run("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null")
            if rc2 == 0 and nvidia:
                result["cuda_available"] = True
                result["nvidia_gpus"]    = [line.strip() for line in nvidia.splitlines()]
                # Extract memory
                mem_matches = re.findall(r"(\d+)\s*MiB", nvidia)
                if mem_matches:
                    result["gpu_memory_gb"] = sum(int(m) for m in mem_matches) / 1024

            # AMD ROCm
            rc3, amd, _ = self._run("rocm-smi --showproductname 2>/dev/null")
            if rc3 == 0 and amd:
                result["rocm_available"] = True

            # Intel iGPU / Arc
            rc4, intel, _ = self._run("ls /dev/dri/ 2>/dev/null")
            if rc4 == 0 and intel:
                result["drm_devices"] = intel.strip().splitlines()

        except Exception as e:
            log.warning(f"GPU detection failed: {e}")
        return result

    # ── Proxmox Specifics ────────────────────────────────────

    def _detect_proxmox(self) -> dict:
        result = {
            "proxmox_version":   "",
            "proxmox_kernel":    "",
            "pve_subscription":  "none",
            "cluster_name":      "",
            "cluster_nodes":     [],
            "vm_count":          0,
            "ct_count":          0,
            "ha_enabled":        False,
        }
        try:
            rc, ver, _ = self._run("pveversion 2>/dev/null")
            if rc == 0: result["proxmox_version"] = ver.strip()

            rc2, kern, _ = self._run("uname -r")
            if rc2 == 0: result["proxmox_kernel"] = kern.strip()

            # Subscription
            rc3, sub, _ = self._run("pvesubscription get 2>/dev/null | grep status")
            if rc3 == 0:
                result["pve_subscription"] = "active" if "active" in (sub or "") else "none"

            # Cluster
            rc4, cluster, _ = self._run("pvecm status 2>/dev/null | head -5")
            if rc4 == 0 and cluster:
                result["cluster_info"] = cluster.strip()
                m = re.search(r"Name:\s*(\S+)", cluster)
                if m: result["cluster_name"] = m.group(1)
                nodes = re.findall(r"Node\s+\d+", cluster)
                result["cluster_nodes"] = nodes

            # VM and CT counts
            rc5, vms, _ = self._run("qm list 2>/dev/null | tail -n +2 | wc -l")
            if rc5 == 0 and vms.strip().isdigit():
                result["vm_count"] = int(vms.strip())

            rc6, cts, _ = self._run("pct list 2>/dev/null | tail -n +2 | wc -l")
            if rc6 == 0 and cts.strip().isdigit():
                result["ct_count"] = int(cts.strip())

            # HA
            rc7, ha, _ = self._run("ha-manager status 2>/dev/null | head -3")
            if rc7 == 0 and ha:
                result["ha_enabled"] = True
                result["ha_status"]  = ha.strip()

        except Exception as e:
            log.warning(f"Proxmox detection failed: {e}")
        return result

    # ── Virtualization ───────────────────────────────────────

    def _detect_virtualization(self) -> dict:
        result = {"is_vm": False, "hypervisor": "", "nested_virt": False}
        try:
            rc, out, _ = self._run("systemd-detect-virt 2>/dev/null")
            if rc == 0:
                virt = out.strip()
                result["is_vm"]      = virt not in ("none", "")
                result["hypervisor"] = virt

            # Nested virtualization
            rc2, nested, _ = self._run("cat /sys/module/kvm_intel/parameters/nested 2>/dev/null || cat /sys/module/kvm_amd/parameters/nested 2>/dev/null")
            if rc2 == 0:
                result["nested_virt"] = nested.strip() in ("1", "Y")

        except Exception as e:
            log.warning(f"Virt detection failed: {e}")
        return result

    # ── ZFS ──────────────────────────────────────────────────

    def _detect_zfs(self) -> dict:
        result = {"zfs_available": False, "zfs_pools": [], "zfs_pool_details": []}
        try:
            rc, out, _ = self._run("zpool list -H -o name,size,alloc,free,health 2>/dev/null")
            if rc == 0 and out:
                result["zfs_available"] = True
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 5:
                        name   = parts[0]
                        health = parts[4]
                        result["zfs_pools"].append(name)
                        result["zfs_pool_details"].append({
                            "name":   name,
                            "size":   parts[1],
                            "alloc":  parts[2],
                            "free":   parts[3],
                            "health": health,
                            "degraded": health != "ONLINE",
                        })

            # ARC stats
            rc2, arc, _ = self._run("cat /proc/spl/kstat/zfs/arcstats 2>/dev/null | grep -E '^size|^hits|^misses' | head -5")
            if rc2 == 0 and arc:
                result["zfs_arc_stats"] = arc.strip()

        except Exception as e:
            log.warning(f"ZFS detection failed: {e}")
        return result

    # ── Ceph ─────────────────────────────────────────────────

    def _detect_ceph(self) -> dict:
        result = {"ceph_available": False, "ceph_status": ""}
        try:
            rc, out, _ = self._run("ceph status 2>/dev/null | head -10")
            if rc == 0 and out:
                result["ceph_available"] = True
                result["ceph_status"]    = out.strip()
                result["ceph_health"]    = "HEALTH_OK" if "HEALTH_OK" in out else "DEGRADED"

        except Exception as e:
            log.warning(f"Ceph detection failed: {e}")
        return result

    # ── OS / Kernel ──────────────────────────────────────────

    def _detect_os(self) -> dict:
        result = {"os_name": "", "kernel": "", "uptime_days": 0}
        try:
            rc, os_info, _ = self._run("cat /etc/os-release | grep PRETTY_NAME")
            if rc == 0:
                result["os_name"] = os_info.split("=",1)[-1].strip().strip('"')

            rc2, kern, _ = self._run("uname -r")
            if rc2 == 0: result["kernel"] = kern.strip()

            rc3, uptime, _ = self._run("cat /proc/uptime")
            if rc3 == 0:
                seconds = float(uptime.split()[0])
                result["uptime_days"]  = round(seconds / 86400, 1)
                result["uptime_hours"] = round(seconds / 3600, 1)

            # Security
            rc4, selinux, _ = self._run("getenforce 2>/dev/null")
            if rc4 == 0: result["selinux"] = selinux.strip()

            rc5, apparmor, _ = self._run("aa-status --enabled 2>/dev/null && echo YES")
            result["apparmor"] = "YES" in (apparmor or "")

        except Exception as e:
            log.warning(f"OS detection failed: {e}")
        return result

    # ── Performance Baseline ─────────────────────────────────

    def _detect_performance(self) -> dict:
        result = {"load_avg": [], "iowait_pct": 0, "cpu_steal_pct": 0}
        try:
            rc, load, _ = self._run("cat /proc/loadavg")
            if rc == 0:
                parts = load.split()
                result["load_avg"] = [float(parts[i]) for i in range(3) if i < len(parts)]

            rc2, iostat, _ = self._run("iostat -c 1 1 2>/dev/null | tail -2 | head -1")
            if rc2 == 0 and iostat:
                vals = iostat.split()
                if len(vals) >= 4:
                    try:
                        result["iowait_pct"]    = float(vals[3])
                        result["cpu_steal_pct"] = float(vals[-1]) if len(vals) > 5 else 0
                    except ValueError:
                        pass

            # IRQ balance
            rc3, irq, _ = self._run("cat /proc/interrupts | wc -l")
            if rc3 == 0: result["irq_count"] = int(irq.strip() or 0)

        except Exception as e:
            log.warning(f"Performance baseline failed: {e}")
        return result

    # ── Helpers ──────────────────────────────────────────────

    def _run(self, cmd: str) -> tuple[int, str, str]:
        if self.ssh:
            return self.ssh.run(cmd, timeout=15)
        # Local execution fallback
        import subprocess
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except Exception as e:
            return -1, "", str(e)

    def summary(self) -> str:
        """One-line human-readable hardware summary."""
        p = self.get()
        if not p:
            return "Hardware profile not yet available"
        parts = [
            f"CPU: {p.get('cpu_model','?')} × {p.get('cpu_cores',0)}c/{p.get('cpu_threads',0)}t",
            f"RAM: {p.get('ram_total_gb',0):.0f}GB",
        ]
        if p.get("nvme_devices"):
            parts.append(f"NVMe: {len(p['nvme_devices'])}")
        if p.get("zfs_pools"):
            parts.append(f"ZFS: {', '.join(p['zfs_pools'])}")
        if p.get("gpus"):
            parts.append(f"GPU: {len(p['gpus'])}")
        if p.get("proxmox_version"):
            parts.append(f"PVE: {p['proxmox_version']}")
        return " | ".join(parts)
