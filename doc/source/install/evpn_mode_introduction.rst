=================================
How it works: OpenStack EVPN mode
=================================

This agent is meant to be executed in all the OpenStack compute nodes
(assuming they are connected to the BGP peers) and ensures that each VM
connected to the local chassis (i.e., local hypervisor) gets its IP advertised
through the proper EVPN if:

- VM is on a network that is tagged to be exposed through EVPN (i.e., with the
  proper BGP_AS and VNI information) and the router the network is connected
  too also has that tag.

The way the agent advertises the VMs is by creating a VRF associated to the
Neutron Router Gateway Port (i.e., the CR-LRP OVN port on the SB DB), based on
the VNI information it has annotated. More specifically, it:

- Creates a VRF device, with routing table matching the VNI number/id

- Creates a Bridge device, associated to the VRF device

- Creates a VXLAN device, associated to the Bridge device, with the local IP as
  the Loopback IP, and the vxlan id matching the VNI number/id

- Creates a dummy device, connected to the VRF, that will be use to expose the
  IPs through BGP (EVPN)

Once that is done, it needs to connect that to the OVN overlay by:

- Adding the VRF device to the provider OVS bridge (e.g., br-ex)

- Adding extra ovs flows to the provider OVS bridge, so that the traffic out
  from OVN is differentiated depending on the router gateway port and network
  CIDR it comes from. This allows to either send the traffic through the VRF
  device or through the standard OVN path (kernel).

  .. code-block:: ini

        cookie=0x3e6, duration=222.137s, table=0, n_packets=0, n_bytes=0, priority=1000,ip,in_port="patch-provnet-c",dl_src=fa:16:3e:74:e6:3b,nw_src=20.0.0.0/24 actions=mod_dl_dst:f2:ff:65:5b:82:4f,output:"vrf-1001"
        cookie=0x0, duration=452321.235s, table=0, n_packets=2637, n_bytes=238529, priority=0 actions=NORMAL

Then, the way the agent advertises the routes is by adding an IP to the dummy
device created that was associated to a vrf. Then it relies on Zebra to do the
BGP advertisement, as Zebra detects the addition/deletion of the IP on the
local interface and create/deletes and advertises/withdraw the route. With
this, to expose a VM IP belonging to a tenant network, it needs to:

- Add the VM IP into the dummy device

- Ensure the local route added for that IP pointing to the dummy device is deleted
  so that traffic can be redirected to the OVS provider bridge

- Add ip route to redirect the traffic towards that subnet CIDR to OVS provider
  bridge, through the CR-LRP port IP, on the VRF routing table (e.g., 1001):

  .. code-block:: ini

        $ ip route show vrf vrf-1001
        unreachable default metric 4278198272
        * 20.0.0.0/24 via 172.24.100.225 dev br-ex*
        * 172.24.100.225 dev br-ex scope link*


.. note::

  - The VMs on tenant networks are exposed through the ovn node where the
    gateway port is located (i.e., the cr-lrp port). That means the traffic
    will go to it first, and then through the geneve tunnel to the node where
    the VM is.


EVPN Watcher Events
-------------------

The OVN-BGP Agent watches the OVN Southbound Database, and the above mentioned
actions are triggered based on the events detected. The agent is reacting to
the next events for the EVPN driver, all of them by watching the Port_Binding
OVN table:

- `PortBindingChassisCreatedEvent` and `PortBindingChassisDeletedEvent`:
  Detects when a port of type “chassisredirect” gets attached to an OVN
  chassis. This is the case for the neutron gateway router ports (CR-LRPs).
  In this case the ip is added to the dummy device associated to the VRF
  if that port has VNI/BGP_AS information tagged. Also the ip route is added
  to the VRF routing table pointing to the OVS provider bridge if the destination
  IP is the CR-LRP one. If there are networks attached to the router, and they
  are also exposed, then extra routes and ovs-flows (as explained above) are
  created too. These events call the driver_api `expose_IP` and `withdraw_IP`.

- `SubnetRouterAttachedEvent` and `SubnetRouterDetachedEvent`: Detects when
  a patch port (whose peer is the “LRP” patch port) gets created or deleted.
  This means a subnet is attached to a router. If the chassis is the one having
  the CR-LRP port for that router where the port is getting created, as the
  port has VNI/BGP_AS information tagged, then the event is processed by the
  agent and the ip routes related to the subnet CIDR are added on the respective
  VRF routing table. In addition extra ovs flows are added to the OVN provider
  bridge to ensure traffic differentiation between different subnets. These
  events call the driver_api `expose_subnet` and `withdraw_subnet`.

- `TenantPortCreatedEvent` and `TenantPortDeletedEvent`: Detects when a port
  of type “” gets updated or deleted. If the chassis where the event is detected
  has the LRP for the network where that port is located (meaning is the node
  with the CR-LRP for the router where the port’s network is connected to), then
  the event is processed and the port IP is added to the dummy device associated
  to the respective VRF. These events call the driver_api `expose_remote_IP` and
  `withdraw_remote_IP`.


EVPN Pre Requisites
-------------------

The agent requires some configuration on the OpenStack nodes:

- FRR installed on the node, with zebra and bgpd daemons enabled.

- FRR configured to expose `/32` IPs from the provider network IP range, e.g:
- 
  .. code-block:: ini

        cat > /etc/frr/frr.conf <<EOF
        frr version 7.0
        frr defaults traditional
        hostname worker1
        no ipv6 forwarding
        !
        router bgp 64999
        bgp router-id 99.99.1.1
        bgp log-neighbor-changes
        neighbor eth1 interface remote-as 64999
        !
        address-family ipv4 unicast
        redistribute connected
        neighbor eth1 allowas-in origin
        neighbor eth1 prefix-list only-host-prefixes out
        exit-address-family
        !
        ip prefix-list only-default permit 0.0.0.0/0
        ip prefix-list only-host-prefixes permit 0.0.0.0/0 ge 32
        !
        ip protocol bgp route-map rm-only-default
        !
        route-map rm-only-default permit 10
        match ip address prefix-list only-default
        set src 99.99.1.1
        !
        line vty
        !
        EOF

- Also, with `l2vpn evpn` enabled. As well as with the next option
  so that the default (ECMP) route can be used to resolve VRF routes,
  allowing its addition to the kernel routing: `ip nht resolve-via-default`

  .. code-block:: ini

        ip nht resolve-via-default

        router bgp 64999
        address-family l2vpn evpn
        neighbor uplink activate
        advertise-all-vni
        advertise ipv4 unicast
        neighbor uplink allowas-in origin
        exit-address-family

- And, until this is automated by the daemon, it is also needed to configure
  the required VNI/VRFs. For example, if we want to allow VRF with VNI 1001 we
  need:

  .. code-block:: ini

      vrf red
      vni 1001
      exit-vrf

      router bgp 64999 vrf red
      address-family ipv4 unicast
      redistribute connected
      exit-address-family
      address-family l2vpn evpn
      advertise ipv4 unicast
      exit-address-family

All this should lead to a routing table like this on the compute nodes:

.. code-block:: ini

        $ ip ro
        default src 99.99.1.1
            nexthop via 100.65.1.1 dev eth1 weight 1
        100.65.1.0/30 dev eth1 proto kernel scope link src 100.65.1.2
        172.24.4.1 via 99.99.1.1 dev lo

        $ ip ro sh vrf red
        unreachable default metric 4278198272
        20.0.0.0/24 via 172.24.100.225 dev br-ex
        172.24.100.225 dev br-ex scope link


How to run with EVPN
--------------------

As a python script on the compute nodes:

.. code-block:: ini

    $ python setup.py install
    $ cat bgp-agent.conf
    [DEFAULT]
    debug=True
    reconcile_interval=120
    driver=osp_ovn_evpn_driver

    $ sudo bgp-agent --config-dir bgp-agent.conf
    Starting BGP Agent...
    Loaded chassis 51c8480f-c573-4c1c-b96e-582f9ca21e70.
    BGP Agent Started...
    ....
