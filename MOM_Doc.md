Date: 19-02-2026

Explore the following topics:
1) iSCSI and LUN concepts
2) kubernetes
3) OS boot procedure
4) s3fs
5) PXE boot, network boot, NFS boot
6) rootfs and PE images
7) Check about lab systems availability for the project setup
8) Read/Write IO

---

Date: 25-02-2026

Project:
1. Explore and configure kubernetes
2. Create kubernetes worker nodes & master nodes locally in a VM locally
  - Configure iSCSI target & initiator in this cluster
  - 4 worker nodes & 2 master nodes
  - Couple of iSCSI clients and initiator

Explore on:
1. How to configure iSCSI target and initiator 
2. How to establish session between iSCSI target and initiator
3. How to configure LUNs on the target
4. How to configure LUNs on the initator
5. How to send I/Os to the LUNs on the initiator

Read articles, wikis and documentation instead of fully relying on chatGPT

Objective: To understand the concepts in-depth so that we can make design decisions confidently

---

Date: 04-03-2026

Project:
1. Explore and configure kubernetes
2. Create kubernetes worker nodes & master nodes locally in a VM locally
  - Configure iSCSI target & initiator in this cluster
  - 4 worker nodes & 2 master nodes
  - Couple of iSCSI clients and initiator

For learning purposes, configure kubernetes cluster locally 
5 nodes:
- 1 master + 4 worker nodes
- Retrieve data from the master node
- Configure 2 worker nodes as iSCSI targets
- Configure 2 worker nodes as iSCSI clients

Simultaneously also try to setup lab systems as soon as possible

Lab Systems Config:
- 4 systems in the k8s cluster
- Configure them as worker and master nodes
- Minimum 2 systems
- To simulate failover atleast 4 systems are needed
- Base OS - Linux SLES Latest version installed

Resources: 
- Configuring iSCSI in Rocky Linux (https://reintech.io/blog/configuring-iscsi-initiator-target-rocky-linux-9)

---

Date: 15-04-2026

1. Insights
- Automating creation of target and client iSCSI
- create 5-10 LUNs with size of less than 50 mb
- add description of software downloaded, commands used for installing and configuring iscsi target and client
- give info about each person's contribution
- just use VMs on lab systems. If able to get it to work on 4 systems. Try to get it to work on lab systems.
- optimize the script to show more metrics. Find important metrics to show. 
- CLI is priority over UI

2. Action items

- finalize list of metrics to be retrieved. 
- mount LUNs on client node
- issue i/o's from the luns on the client system. observe metrics on corresponding luns of target system
- map target client luns
- add Kubernetes label to iscsi targets only
- application should only retrieve metrics from labeled nodes
- create general worker nodes to differentiate labeled iscsi targets and unlabeled
- count number of targets
- create luns using empty/dummy disk files

3. Metrics list

- LUNwise -(4 items+ read_mbytes,write_mbytes,num_cmds(iops))
- count number of targets configured and labeled as iscsi targets
- count number of targets not configured/labeled as iscsi targets
- give metrics of types of images in each worker node
- total number of targets
- network metrics

---
