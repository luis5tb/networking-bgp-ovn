==================================
bgp-ovn service installation guide
==================================

.. toctree::
   :maxdepth: 2

   bgp_mode_introduction.rst
   evpn_mode_introduction.rst

The bgp-ovn service (networking_bgp_ovn) provides an agent that targets to
expose VMs/Containers through BGP and/or EVPN on OVN environments.

It provides a multi driver implementation that allows you to configure it
for specific infrastructure running on OVN. For now there is only drivers for
OpenStack, but similar ones could be added for Kubernetes/OpenShift. It
defines what events it should react to.

For instance, in OpenStack case, for the BGP mode:

- To VMs being created on provider networks

- To VMs with attached floating ips

- (optionally) Any VM on tenant networks assuming no IP overlap between tenants

And for Kubernetes/OpenShift it could be:

- Services of LoadBalancer type being created

A common driver API is defined exposing the next methods:

- expose_IP and withdraw_IP: used to expose/withdraw IPs for local ovn ports,
  such as local VMs or Pods.

- expose_remote_IP and withdraw_remote_IP: use to expose/withdraw IPs through
  the local node when the VM/Pod are running on a different node. For example
  for VMs on tenant networks where the traffic needs to be injected through
  the OVN router gateway port.

- expose_subnet and withdraw_subnet: used to expose/withdraw subnets through
  the local node.

Note only the code (i.e., drivers and specific watchers) for OpenStack is there
at the moment. There are two different drivers for OpenStack:

- osp_ovn_bgp_driver: this driver exposes through BGP the IPs of VMs on
  provider networks and the FIPs associated to VMs on tenant networks, as well
  as the VM IPs on the tenant networks if the `expose_tenant_network` config
  option is set to `True`. The code is on the driver file `ovn_bgp_driver.py`,
  and the watcher that it uses is `bgp_watcher.py`.

- osp_ovn_evpn_driver: this driver exposes through EVPN the IPs of the VMs on
  the tenant networks, based on the provider information related to
  `BGP_AS` and `VNI` to use for the EVPN. The code is on the driver file
  `ovn_evpn_driver.py`, and the watcher that it uses is `evpn_watcher.py`.

This chapter assumes a working setup of OpenStack following the
`OpenStack Installation Tutorial
<https://docs.openstack.org/project-install-guide/ocata/>`_.
