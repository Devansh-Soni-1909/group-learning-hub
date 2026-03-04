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

---
