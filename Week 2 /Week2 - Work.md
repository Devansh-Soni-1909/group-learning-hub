# Configure iSCSI Target & Initator

My goal is to try running everything locally once and then think about shifting them inside a kubernetes cluster

# S3 Server Reason

We need to host a S3 bucket server to store the base images and to use it a backstore using s3fs

Upon searching, the only lightweight open source s3 software i could find was minio ( https://www.min.io/ )

Since we are considering setting up a kubernetes cluster, we need a docker image for minio run, therefore found the docker image in this docker hub ( https://hub.docker.com/r/minio/minio )

# Configuring Host

1. Install docker
2. Pull and run minios3 docker image
   https://hub.docker.com/r/minio/minio
3. Login to the web console and create a bucket to store the os images

# VM OS Reason

All the enterprise machines use Red Hat Enterprise Linux (RHEL) for their VMs.Upon Searching for how to configure iSCSI, I found this HPE blog explaining the steps on Red Hat Linux:

- https://support.hpe.com/hpesc/public/docDisplay?docId=sf000075154en_us&docLocale=en_US

I also found a blog by RedHat on How to confiure iSCSI target and initiator:

- https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/managing_storage_devices/configuring-an-iscsi-target#creating-a-fileio-storage-object

To create a setup as close to that for free, we need to find a open source version compatible with it.

I intially found CentOS by Red Hat themselves but it was discontinued.

Then found redhat Universal Base Images, these are present as docker images but they don't grant access all the RHEL services.

So upon searching, found Rocky linux ( https://rockylinux.org/ ) which is its closest open source version.

# Configuring s3fs with Minio

1.  Install Virtual Machine Manager packages

    `sudo apt install qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virt-manager`

2.  Download Rocky Linux 8 - Minimal ISO Image
    https://dl.rockylinux.org/pub/rocky/8/isos/x86_64/

3.  Create a VM in VMM using the rocky linux iso image

4.  Login to the console and check the network connectivity

        ping 8.8.8.8

- If not connected to network, Check the ethernet connection

        nmcli connection show

- if disconnected, turn it on by

        nmcli connection up enp1s0

5. Install s3fs-fuse (https://github.com/s3fs-fuse/s3fs-fuse)

6. Setup Minio with s3fs
   - https://github.com/nitisht/cookbook/blob/master/docs/s3fs-fuse-with-minio.md
   - https://www.iblue.team/general-notes-1/s3fs-fuse-and-minio

# Configuring iSCSI target
