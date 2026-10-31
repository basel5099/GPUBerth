# Install on Proxmox VE

These steps describe the setup that GPUBerth came from. They have not yet been run from scratch in
this generic form, so please report what you find. Addresses below are examples.

## 1. GPU host

1. Enable IOMMU and bind the GPUs to `vfio-pci`.
2. Create one PCI mapping per GPU (Datacenter > Resource Mappings). A mapping refuses to start if a
   card moves to another PCIe address, instead of taking the wrong device.
3. Optional power saving: enable Wake-on-LAN in the BIOS and with `ethtool -s <nic> wol g`, and make
   the setting persist across boots.

## 2. Networks

Create one bridge per seat, with no physical port, no host address and IPv6 disabled:

```text
auto vmbr3
iface vmbr3 inet manual
    bridge-ports none
    bridge-stp off
    bridge-fd 0
    post-up echo 1 > /proc/sys/net/ipv6/conf/vmbr3/disable_ipv6
```

On the firewall, add one interface per seat. Rules, in order:

1. Block the seat network to an alias with all private ranges, link-local, CGNAT and your public IPs.
2. Block TCP 25.
3. Allow the seat network to any.

Forward each seat's SSH port and service ports from the public IP to that seat VM only.

## 3. Agent VM image

1. Install Ubuntu 24.04 with cloud-init, UEFI, q35, no guest agent and no Secure Boot keys.
2. Install the NVIDIA driver. RTX 50-series cards need `nvidia-driver-<version>-open`.
3. Install `nfs-common`, `podman` and `nvidia-container-toolkit`. Mask `nvidia-cdi-refresh`.
4. Copy `guest/gpuberth-cdi` to `/usr/local/sbin/`.
5. Create the normal user `agent` without sudo or password, in groups `video` and `render`.
6. Create an admin user with sudo and the broker's `vmadmin` public key.
7. Configure sshd:

   ```text
   PasswordAuthentication no
   PermitRootLogin no
   AllowUsers <admin-user> agent
   TrustedUserCAKeys /etc/ssh/agent_ca.pub
   AuthorizedPrincipalsFile /etc/ssh/principals/%u
   ```

8. Copy the broker's `ca.pub` to `/etc/ssh/agent_ca.pub`.
9. Shut down and take a snapshot named `clean`, with no GPUs attached.
10. Clone from the snapshot for each further seat, change the bridge and IP, boot once, shut down,
    and take its own `clean` snapshot.

`app.py` uses the admin user `gpuadmin` by default.

## 4. Storage VM

1. Create a small VM with one network card on every seat bridge and the guest agent enabled.
2. Add a data disk with an ext4 file system, mounted at `/srv`.
3. Install `nfs-kernel-server`, disable NFSv3, and set `net.ipv4.ip_forward=0`.
4. Copy `storage/gpuberth-share` to `/usr/local/sbin/` and create `/etc/gpuberth/storage.env`.
5. Use nftables to allow only TCP 2049 from the seat IPs. Drop all other input, output and forwarding.

## 5. Broker

```bash
useradd --system --home /var/lib/gpuberth --shell /usr/sbin/nologin gpuberth
install -d -o gpuberth -g gpuberth -m 700 /var/lib/gpuberth /var/lib/gpuberth/keys
python3 -m venv /opt/gpuberth/venv
/opt/gpuberth/venv/bin/pip install -r broker/requirements.txt
cp -r broker /opt/gpuberth/
cp deploy/gpuberth.service /etc/systemd/system/
mkdir -p /etc/gpuberth && cp deploy/config.example.toml /etc/gpuberth/config.toml   # then edit it
cd /var/lib/gpuberth
sudo -u gpuberth ssh-keygen -t ed25519 -N "" -f ca
for k in control wake vmadmin; do sudo -u gpuberth ssh-keygen -t ed25519 -N "" -f keys/$k; done
systemctl enable --now gpuberth
sudo -u gpuberth /opt/gpuberth/venv/bin/python /opt/gpuberth/broker/gpuberth-admin add owner --admin
```

Put HTTPS in front of port 8080, and allow that port only from your reverse proxy.

## 6. Connect the broker

On the GPU host, copy `host/gpuberth-ctl` to `/usr/local/sbin/`, create `/etc/gpuberth/host.env`, and
add the `control` public key with the locked options shown at the top of that script.
Repeat for `gpuberth-wake` and the `wake` key on the always-on machine.

## 7. Test

Run the checks in [SECURITY.md](../SECURITY.md), then make a booking for all GPUs, two smaller
bookings at the same time, and a booking while the host is off.
