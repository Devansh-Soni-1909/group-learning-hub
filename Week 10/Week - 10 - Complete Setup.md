# Complete Setup Commands

## 1. Create VMs

- 1 Master + 3 Worker Node VMs
- OS = Ubuntu Server 24.04
- Create 1 master-node vm with name master-node
- Create 2 iscsi-target vms with names iscsi-target-1 & iscsi-target-2
- Create 1 iscsi-client vm with name iscsi-client-1

- Notes: Install OpenSSH Server during Ubuntu Installation

## 2. Configure iSCSI Targets and Clients

### 1. iSCSI Target

Setup:

- 10 LUNs ( Size = 50MB )

```
Target
└── TPG1
    ├── lun0 -> disk01
    ├── lun1 -> disk02
    ├── ...
    ├── lun9 -> disk10
    └── ACLs
         └── initiator IQN
```

To configure a new ubuntu VM as iscsi-target with above architecture, run the following commands:

1. Create a script file

```
nano iscsi-target-setup.sh
```

2. Copy the below script into the file

```bash
#!/bin/bash
set -e

BASE_DIR="/var/lib/iscsi_disks"
PORTAL_IP="0.0.0.0"
PORTAL_PORT="3260"

CLIENT1="iqn.2026-04.lab.local:node1.initiator"

CHAP_USER="username"
CHAP_PASS="password"

TARGET_IQN="iqn.2026-04.lab.local:lab.target01"

echo "[+] Installing targetcli"
apt-get update
apt-get install -y targetcli-fb

echo "[+] Creating disk directory"
mkdir -p ${BASE_DIR}

echo "[+] Cleaning old disk images"
rm -f ${BASE_DIR}/disk\*.img

echo "[+] Resetting existing target configuration"
targetcli clearconfig confirm=True || true

echo "[+] Creating 10 fileio backstore disks"

for i in $(seq -w 1 10); do
    targetcli /backstores/fileio create \
        disk${i} \
${BASE_DIR}/disk${i}.img \
50M
done

echo "[+] Creating iSCSI target"
targetcli /iscsi create ${TARGET_IQN}

echo "[+] Creating portal"
targetcli /iscsi/${TARGET_IQN}/tpg1/portals create \
${PORTAL_IP} ${PORTAL_PORT} || true

echo "[+] Enabling CHAP authentication"
targetcli /iscsi/${TARGET_IQN}/tpg1 \
set attribute authentication=1

echo "[+] Creating ACL for initiator"
targetcli /iscsi/${TARGET_IQN}/tpg1/acls create ${CLIENT1}

echo "[+] Configuring CHAP credentials"
targetcli /iscsi/${TARGET_IQN}/tpg1/acls/${CLIENT1} \
set auth userid=${CHAP_USER}

targetcli /iscsi/${TARGET_IQN}/tpg1/acls/${CLIENT1} \
set auth password=${CHAP_PASS}

echo "[+] Mapping all 10 disks as LUNs"

for i in $(seq -w 1 10); do
    targetcli /iscsi/${TARGET_IQN}/tpg1/luns create \
/backstores/fileio/disk${i}
done

echo "[+] Saving configuration"
targetcli saveconfig

echo "[+] Enabling target service"
systemctl enable rtslib-fb-targetctl

echo "[+] Setup complete"
```

3. Give execution permission to the script

```

chmod +x iscsi-target-setup.sh

```

4. Run the script as sudo user

```

sudo ./iscsi-target-setup.sh

```

5. Verify the setup

```

sudo targetcli ls

```

### 2. iSCSI Client

To configure a new ubuntu VM as iscsi-client to work with above iscsi-setup, run the following commands:

1. Create a script file

   ```

   nano iscsi-client-setup.sh

   ```

2. Copy the below code to the script and modify the `TARGET_IP` and `CLIENT_IQN` if needed

   ```bash
   #!/bin/bash
   set -e

   # Add port if needed (eg: "192.168.122.197:3261" ), default port is 3260
   PORTAL="192.168.122.189:3260"

   CLIENT_IQN="iqn.2026-04.lab.local:node1.initiator"
   CHAP_USER="username"
   CHAP_PASS="password"

   echo "[+] Clearing previous iscsi-client configs"
   systemctl stop open-iscsi || true
   systemctl stop iscsid || true
   iscsiadm -m node --logout || true
   iscsiadm -m node -o delete || true
   rm -rf /etc/iscsi/nodes/*
   rm -rf /etc/iscsi/send_targets/*
   apt purge open-iscsi -y

   echo "[+] Installing open-iscsi"
   apt-get update -y
   apt-get install -y open-iscsi

   echo "[+] Setting initiator IQN"
   sed -i "s|^InitiatorName=.*|InitiatorName=${CLIENT_IQN}|" /etc/iscsi/initiatorname.iscsi

   echo "[+] Configuring CHAP authentication"
   # Enable CHAP
   sed -i 's|^#*node.session.auth.authmethod.*|node.session.auth.authmethod = CHAP|' /etc/iscsi/iscsid.conf

   # Set username/password
   sed -i "s|^[[:space:]]*#*node.session.auth.username.*|node.session.auth.username = ${CHAP_USER}|" /etc/iscsi/iscsid.conf
   sed -i "s|^[[:space:]]*#*node.session.auth.password.*|node.session.auth.password = ${CHAP_PASS}|" /etc/iscsi/iscsid.conf

   echo "[+] Restarting services"
   systemctl restart iscsid
   systemctl restart open-iscsi

   echo "[+] Discovering targets"
   iscsiadm -m discovery -t sendtargets -p ${PORTAL}

   echo "[+] Logging into all discovered targets"
   iscsiadm -m node --login || true

   echo "[+] Enabling auto-login on boot"
   iscsiadm -m node -o update -n node.startup -v automatic

   echo "[+] Verifying sessions"
   iscsiadm -m session

   echo "[+] Checking block devices"
   lsblk

   echo "iSCSI client setup complete"
   ```

3. Verify the session and see the lun to disks mapping

   ```
   sudo iscsiadm -m session -P 3
   ```

4. Perform I/O operations on the disk
   - Write IO

   ```
   sudo dd if=/dev/zero of=/dev/sda bs=1M count=10 status=progress
   ```

   - Read IO

   ```
   sudo dd if=/dev/sda of=/dev/null bs=1M count=10 status=progress
   ```

5. Check the iSCSI target in path `/sys/kernel/config/target` for metrics

## 3. Install Kubernetes in all the VMs

### 1. Containerd Setup:

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
     $ wget https://github.com/containerd/containerd/releases/download/v2.3.0/containerd-2.3.0-linux-amd64.tar.gz
     ```
     ```
     $ sudo tar Cxzvf /usr/local containerd-2.3.0-linux-amd64.tar.gz
     bin/
     bin/containerd-shim-runc-v2
     bin/containerd-shim
     bin/ctr
     bin/containerd-shim-runc-v1
     bin/containerd
     bin/containerd-stress
     ```
   - Configure systemd
     Download the containerd.service unit file from https://raw.githubusercontent.com/containerd/containerd/main/containerd.service into `/usr/local/lib/systemd/system/containerd.service`:

     ```
     wget https://raw.githubusercontent.com/containerd/containerd/main/containerd.service
     sudo mkdir /usr/local/lib/systemd
     sudo mkdir /usr/local/lib/systemd/system
     sudo mv containerd.service /usr/local/lib/systemd/system
     ```

     Enable the containerd service

     ```
     systemctl daemon-reload
     systemctl enable --now containerd
     ```

   - Install runc
     Download the runc.<ARCH> binary from https://github.com/opencontainers/runc/releases , verify its sha256sum, and install it as /usr/local/sbin/runc.
     ```
     wget https://github.com/opencontainers/runc/releases/download/v1.4.2/runc.amd64
     sudo install -m 755 runc.amd64 /usr/local/sbin/runc
     ```
   - Install CNI plugins
     Download the cni-plugins-<OS>-<ARCH>-<VERSION>.tgz archive from https://github.com/containernetworking/plugins/releases , verify its sha256sum, and extract it under /opt/cni/bin:
     ```
     $ wget https://github.com/containernetworking/plugins/releases/download/v1.9.1/cni-plugins-linux-amd64-v1.9.1.tgz
     $ sudo mkdir -p /opt/cni/bin
     $ sudo tar Cxzvf /opt/cni/bin cni-plugins-linux-amd64-v1.9.1.tgz
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
   - Generate the defauly config file

   ```
    sudo mkdir -p /etc/containerd
    containerd config default | sudo tee /etc/containerd/config.toml
   ```

   - Open the file at location `/etc/containerd/config.toml.`
     To use the systemd cgroup driver in `/etc/containerd/config.toml` with runc, set the following config: (Containerd versions 2.x)

   ```
   sudo nano /etc/containerd/config.toml
   ```

   ```
   [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runc]
       [plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runc.options]
       SystemdCgroup = true
   ```

   - You need CRI support enabled to use containerd with Kubernetes. Make sure that cri is not included in thedisabled_plugins list within `/etc/containerd/config.toml`. If you made changes to that file, also restart containerd.

   ```
   sudo systemctl restart containerd
   ```

- Reference: https://v1-34.docs.kubernetes.io/docs/setup/production-environment/container-runtimes/, https://github.com/containerd/containerd/blob/main/docs/getting-started.md

### 2. Install kubeadm, kubectl, kubelet:

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

- Reference: https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/install-kubeadm/

### 3. Configure Kubernetes Cluster:

1.  Create a cluster
    `    sudo kubeadm init --apiserver-advertise-address=192.168.122.244 --pod-network-cidr=10.244.0.0/16`

    192.168.122.244 - IP address of master node on the control plane in the VM network

    ```
    mkdir -p $HOME/.kube
    sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
    sudo chown $(id -u):$(id -g) $HOME/.kube/config
    ```

2.  Install CNI - flannel

    ```
    kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml
    ```

3.  Join the worker nodes to the cluster
    Run this command on every worker node VM

    ```
    kubeadm join 192.168.122.1:6443 --token 5vzqq9.qk5hdaobfoshd3nf \
    --discovery-token-ca-cert-hash sha256:f7e1f9022eed85a2fb10f6e203f255f60b97c65d18ccc240f409ad172d8a2008
    ```

    Replace the command with what you get after initialization

4.  Add iscsi-target label to target nodes in k8s cluster

    ```
     kubectl label node iscsi-target-1 node-role.kubernetes.io/iscsi-target=true --overwrite
     kubectl label node iscsi-target-2 node-role.kubernetes.io/iscsi-target=true --overwrite
    ```

5.  Error Handling

    If kube flannel pod is failing for the node with the error:

    ```
    Failed to check br_netfilter: stat /proc/sys/net/bridge/bridge-nf-call-iptables: no such file or directory
    ```

    Run these commands in the worker node:

    ```
    sudo modprobe br_netfilter
    sudo modprobe bridge
    sudo modprobe vxlan
    sudo modprobe overlay
    ```

    ```
    printf "overlay\nbr_netfilter\nvxlan\n" | sudo tee

    cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
    net.ipv4.ip_forward=1
    net.bridge.bridge-nf-call-iptables=1
    net.bridge.bridge-nf-call-ip6tables=1
    EOF
    ```

    ```
    sudo sysctl --system
    ```

    ```
    sudo systemctl restart containerd
    sudo systemctl restart kubelet
    ```

    Run these commands in the control plane:

    ```
    kubectl delete pods -n kube-flannel --all
    kubectl get pods -n kube-flannel -w
    kubectl get nodes -w
    ```

        - Reference: https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/

## 4. Build & Push Docker Image

1. Build the docker image from the DockerFile in the setup folder

   ```
   docker build -t iscsi-http:latest -f docker/Dockerfile.iscsi.http .
   ```

2. Tag the docker image ( Replace notmybug with your dockerhub username )

   ```
   docker tag iscsi-http:latest notmybug/iscsi-http:latest
   ```

3. Login to dockerhub

   ```
   docker login
   ```

4. Push the docker image (Replace notmybug with your dockerhub username)

   ```
   docker push notmybug/iscsi-http:latest
   ```

View my docker image here: https://hub.docker.com/r/notmybug/iscsi-http

## 5. Run Daemonset and Service

1. Create a new namespace for iscsi

   ```
   kubectl create namespace iscsi
   ```

1. Apply the `iscsi-http.yaml` to the kubernetes cluster

   ```
   kubectl -n iscsi apply -f setup/iscsi-http.yaml
   ```

1. Verify the status of daemonset and service deployed

   ```
   kubectl -n iscsi rollout status daemonset/iscsi-target-http
   kubectl -n iscsi get pods -l app=iscsi-target-http -o wide
   kubectl -n iscsi get ds iscsi-target-http -o yaml
   ```

## 6. Test the HTTP endpoint

1. Port forward the service

   ```
   kubectl -n iscsi port-forward service/iscsi-target-http 9000:9000
   ```

2. Test the endpoint using curl or open the url in the browser

   ```
   curl http://localhost:9000/metrics/flat
   curl http://localhost:9000/info
   curl http://localhost:9000/metrics/flat
   ```

## 7. Fetch metrics using python CLI

1. Install kubernetes python client

   ```
   sudo apt update
   sudo apt install python3-kubernetes
   ```

2. Copy the python script to a local file
3. Run the file
   ```
   python3 iscsi-metrics-cli.py
   ```
