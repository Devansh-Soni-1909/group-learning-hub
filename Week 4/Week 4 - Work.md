# Configuring Kubernetes (k8s) for iSCSI

## Setup Plan

- Control Plane: Host OS (Ubuntu Desktop)
- Data Plane: 2 Ubuntu Server VMs (iscsi Client VM + iscsi Target VM)

## Kubernetes Installation:

Install the follwing in both all the nodes in both control and data plane.

- Container Runtime Interface - containerd
- kubeadm
- kubelet
- kubectl (Only in control plane nodes)

### 1. Containerd Setup:

- Reference: https://v1-34.docs.kubernetes.io/docs/setup/production-environment/container-runtimes/, https://github.com/containerd/containerd/blob/main/docs/getting-started.md
  1. Install and configure prerequisites
     - Enable IPv4 packet forwarding

       ```
       # sysctl params required by setup, params persist across reboots
       cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
       net.ipv4.ip_forward = 1
       EOF

       # Apply sysctl params without reboot
       sudo sysctl --system
       ```

       Verify that net.ipv4.ip_forward is set to 1 with:

       ```
       sysctl net.ipv4.ip_forward
       ```

  2. Install Containerd
     - Download the containerd-<VERSION>-<OS>-<ARCH>.tar.gz archive from https://github.com/containerd/containerd/releases , verify its sha256sum, and extract it under /usr/local:
       ```
       $ tar Cxzvf /usr/local containerd-2.2.2-linux-amd64.tar.gz
       bin/
       bin/containerd-shim-runc-v2
       bin/containerd-shim
       bin/ctr
       bin/containerd-shim-runc-v1
       bin/containerd
       bin/containerd-stress
       ```
     - Configure systemd
       Download the containerd.service unit file from https://raw.githubusercontent.com/containerd/containerd/main/containerd.service into /usr/local/lib/systemd/system/containerd.service, and run the following commands:
       ```
       systemctl daemon-reload
       systemctl enable --now containerd
       ```
     - Install runc
       Download the runc.<ARCH> binary from https://github.com/opencontainers/runc/releases , verify its sha256sum, and install it as /usr/local/sbin/runc.
       ```
       install -m 755 runc.amd64 /usr/local/sbin/runc
       ```
     - Install CNI plugins
       Download the cni-plugins-<OS>-<ARCH>-<VERSION>.tgz archive from https://github.com/containernetworking/plugins/releases , verify its sha256sum, and extract it under /opt/cni/bin:
       ```
       $ mkdir -p /opt/cni/bin
       $ tar Cxzvf /opt/cni/bin cni-plugins-linux-amd64-v1.1.1.tgz
       ./
       ./macvlan
       ./static
       ./vlan
       ./portmap
       ./host-local
       ./vrf
       ./bridge
       ./tuning
       ./firewall
       ./host-device
       ./sbr
       ./loopback
       ./dhcp
       ./ptp
       ./ipvlan
       ./bandwidth
       ```
  3. Configure systemd cgroup driver
     Open the file at location `/etc/containerd/config.toml.`
     To use the systemd cgroup driver in /etc/containerd/config.toml with runc, set the following config: (Containerd versions 2.x)

  ```
  [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runc]
      [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runc.options]
      SystemdCgroup = true
  ```

  You need CRI support enabled to use containerd with Kubernetes. Make sure that cri is not included in thedisabled_plugins list within `/etc/containerd/config.toml`
  if you made changes to that file, also restart containerd.

  ```
  sudo systemctl restart containerd
  ```

### 2. Install kubeadm, kubectl, kubelet:

- Reference: https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/install-kubeadm/
  1. Turn the swap memory off: `sudo swapoff -a`
  2. Make sure the containerd runtime is installed
  3. Install kubeadm, kubectl, kubelet

  ```
  sudo apt-get update
  # apt-transport-https may be a dummy package; if so, you can skip that package
  sudo apt-get install -y apt-transport-https ca-certificates curl gpg

  # If the directory `/etc/apt/keyrings` does not exist, it should be created before the curl command, read the note below.
  # sudo mkdir -p -m 755 /etc/apt/keyrings
  curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.35/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg

  # This overwrites any existing configuration in /etc/apt/sources.list.d/kubernetes.list
  echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.35/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list

  sudo apt-get update
  sudo apt-get install -y kubelet kubeadm kubectl
  sudo apt-mark hold kubelet kubeadm kubectl

  sudo systemctl enable --now kubelet
  ```

### 3. Configure Kubernetes Cluster:

- Reference: https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/
  1. Create a cluster

  ```
  sudo kubeadm init --apiserver-advertise-address=192.168.122.1
  ```

  192.168.122.1 - IP address of host machine on the control plane in the VM network 3. Setup CNI

  ```
  mkdir -p $HOME/.kube
  sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
  sudo chown $(id -u):$(id -g) $HOME/.kube/config
  ```

  2. Install CNI - flannel

  ```
  kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml
  ```

  3. Join the worker nodes to the cluster
     Run this command on every worker node VM

  ```
  kubeadm join 192.168.122.1:6443 --token 5vzqq9.qk5hdaobfoshd3nf \
  --discovery-token-ca-cert-hash sha256:f7e1f9022eed85a2fb10f6e203f255f60b97c65d18ccc240f409ad172d8a2008
  ```

  Replace the command with what you get after initialization 4. If kube flannel pod is failing for the node with the error:

  ```
  Failed to check br_netfilter: stat /proc/sys/net/bridge/bridge-nf-call-iptables: no such file or directory
  ```

  Run these commands in the worker node:

  ```
  sudo modprobe br_netfilter
  sudo modprobe bridge
  sudo modprobe vxlan
  sudo modprobe overlay

  sudo apt update
  sudo apt install -y linux-modules-extra-$(uname -r)
  sudo reboot

  printf "overlay\nbr_netfilter\nvxlan\n" | sudo tee

  cat <<EOF | sudo tee k8s.conf
  net.ipv4.ip_forward=1
  net.bridge.bridge-nf-call-iptables=1
  net.bridge.bridge-nf-call-ip6tables=1
  EOF
  sudo sysctl --system

  sudo systemctl restart containerd
  sudo systemctl restart kubelet
  ```

  Run these commands in the control plane:

  ```
  kubectl delete pod -n kube-flannel kube-flannel-<pod_name>
  kubectl get pods -n kube-flannel -w
  kubectl get nodes -w
  ```

## iSCSI Target & Client Setup

### iSCSI Target setup in ubuntu:

- Using Targetcli: https://www.server-world.info/en/note?os=Ubuntu_22.04&p=iscsi&f=1
- Using tgt: https://www.server-world.info/en/note?os=Ubuntu_22.04&p=iscsi&f=2

I have configured using targetcli

### iSCSI Client setup in ubuntu:

- https://www.server-world.info/en/note?os=Ubuntu_22.04&p=iscsi&f=3

## Setup Snapshots

### Master Node - Host OS - Control Plane

- Cluster Creation
  ![k8s cluster creation](image-4.png)

- CNI Flannel Setup

  ![k8s cluster cni flannel config](image-5.png)

- Cluster Nodes

  ![k8s cluster nodes](image-3.png)

### Worker Nodes - Ubuntu Server VMs - Data Plane

- iSCSI Target Config (LUN size = 100MB)
  ![iscsi-target config](image-2.png)

- iSCSI Client Config
  ![iscsi-client login](image.png)
  ![iscsi-client lun](image-1.png)

## Notes:

- Add description to the commands ran
