# Copyright 2021 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from oslo_config import cfg

from ovs.stream import Stream
from ovsdbapp.backend import ovs_idl
from ovsdbapp.backend.ovs_idl import connection
from ovsdbapp.backend.ovs_idl import idlutils
from ovsdbapp import event
from ovsdbapp.schema.ovn_southbound import impl_idl as sb_impl_idl

from networking_bgp_ovn import constants

CONF = cfg.CONF


class OvnIdl(connection.OvsdbIdl):
    def __init__(self, driver, remote, schema):
        super(OvnIdl, self).__init__(remote, schema)
        self.driver = driver
        self.notify_handler = OvnDbNotifyHandler(driver)
        self.event_lock_name = "neutron_ovn_event_lock"

    def notify(self, event, row, updates=None):
        if self.is_lock_contended:
            return
        self.notify_handler.notify(event, row, updates)


class OvnDbNotifyHandler(event.RowEventHandler):
    def __init__(self, driver):
        super(OvnDbNotifyHandler, self).__init__()
        self.driver = driver


class OvnSbIdl(OvnIdl):
    SCHEMA = 'OVN_Southbound'

    def __init__(self, connection_string, chassis=None, events=None,
                 tables=None):
        if connection_string.startswith("ssl"):
            self._check_and_set_ssl_files(self.SCHEMA)
        helper = self._get_ovsdb_helper(connection_string)
        self._events = events
        if tables is None:
            tables = ('Chassis', 'Encap', 'Port_Binding', 'Datapath_Binding',
                      'SB_Global')
        for table in tables:
            helper.register_table(table)
        super(OvnSbIdl, self).__init__(
            None, connection_string, helper)
        if chassis:
            table = ('Chassis_Private' if 'Chassis_Private' in tables
                     else 'Chassis')
            self.tables[table].condition = [['name', '==', chassis]]

    def _get_ovsdb_helper(self, connection_string):
        return idlutils.get_schema_helper(connection_string, self.SCHEMA)

    def _check_and_set_ssl_files(self, schema_name):
        priv_key_file = CONF.ovn_sb_private_key
        cert_file = CONF.ovn_sb_certificate
        ca_cert_file = CONF.ovn_sb_ca_cert

        if priv_key_file:
            Stream.ssl_set_private_key_file(priv_key_file)

        if cert_file:
            Stream.ssl_set_certificate_file(cert_file)

        if ca_cert_file:
            Stream.ssl_set_ca_cert_file(ca_cert_file)

    def start(self):
        conn = connection.Connection(
            self, timeout=180)
        ovsdbSbConn = OvsdbSbOvnIdl(conn)
        if self._events:
            self.notify_handler.watch_events(self._events)
        return ovsdbSbConn


class Backend(ovs_idl.Backend):
    lookup_table = {}
    ovsdb_connection = None

    def __init__(self, connection):
        self.ovsdb_connection = connection
        super(Backend, self).__init__(connection)

    @property
    def idl(self):
        return self.ovsdb_connection.idl

    @property
    def tables(self):
        return self.idl.tables


class OvsdbSbOvnIdl(sb_impl_idl.OvnSbApiIdlImpl, Backend):
    def __init__(self, connection):
        super(OvsdbSbOvnIdl, self).__init__(connection)
        self.idl._session.reconnect.set_probe_interval(60000)

    def _get_port_by_name(self, port):
        cmd = self.db_find_rows('Port_Binding', ('logical_port', '=', port))
        port_info = cmd.execute(check_error=True)
        if port_info:
            return port_info[0]
        return []

    def _get_ports_by_datapath(self, datapath, port_type=None):
        if port_type:
            cmd = self.db_find_rows('Port_Binding',
                                    ('datapath', '=', datapath),
                                    ('type', '=', port_type))
        else:
            cmd = self.db_find_rows('Port_Binding',
                                    ('datapath', '=', datapath))
        return cmd.execute(check_error=True)

    def is_provider_network(self, datapath):
        cmd = self.db_find_rows('Port_Binding', ('datapath', '=', datapath),
                                ('type', '=', 'localnet'))
        return next(iter(cmd.execute(check_error=True)), None)

    def get_fip_associated(self, port):
        cmd = self.db_find_rows('Port_Binding', ('type', '=', 'patch'))
        for row in cmd.execute(check_error=True):
            for fip in row.nat_addresses:
                if port in fip:
                    return fip.split(" ")[1], row.datapath
        return None, None

    def is_port_on_chassis(self, port_name, chassis):
        port_info = self._get_port_by_name(port_name)
        try:
            if (port_info and port_info.type == "" and
                    port_info.chassis[0].name == chassis):
                return True
        except IndexError:
            pass
        return False

    def is_port_deleted(self, port_name):
        port_info = self._get_port_by_name(port_name)
        if port_info:
            return False
        return True

    def get_ports_on_chassis(self, chassis):
        rows = self.db_list_rows('Port_Binding').execute(check_error=True)
        return [r for r in rows if r.chassis and r.chassis[0].name == chassis]

    def get_network_name_and_tag(self, datapath, bridge_mappings):
        for row in self._get_ports_by_datapath(datapath, 'localnet'):
            if (row.options and
                    row.options.get('network_name') in bridge_mappings):
                return row.options.get('network_name'), row.tag
        return None, None

    def get_network_vlan_tag_by_network_name(self, network_name):
        cmd = self.db_find_rows('Port_Binding', ('type', '=', 'localnet'))
        for row in cmd.execute(check_error=True):
            if (row.options and
                    row.options.get('network_name') == network_name):
                return row.tag
        return None

    def is_router_gateway_on_chassis(self, datapath, chassis):
        port_info = self._get_ports_by_datapath(datapath, 'chassisredirect')
        try:
            if port_info and port_info[0].chassis[0].name == chassis:
                return port_info[0].logical_port
        except IndexError:
            pass
        return None

    def get_lrp_port_for_datapath(self, datapath):
        for row in self._get_ports_by_datapath(datapath, 'patch'):
            if row.options:
                return row.options['peer']
        return None

    def get_lrp_ports_for_router(self, datapath):
        return self._get_ports_by_datapath(datapath, 'patch')

    def get_port_datapath(self, port_name):
        port_info = self._get_port_by_name(port_name)
        if port_info:
            return port_info.datapath
        return None

    def get_ports_on_datapath(self, datapath):
        return self._get_ports_by_datapath(datapath)

    def get_evpn_info_from_crlrp_port_name(self, port_name):
        router_gateway_port_name = port_name.split('cr-lrp-')[1]
        return self.get_evpn_info_from_port_name(router_gateway_port_name)

    def get_evpn_info_from_lrp_port_name(self, port_name):
        router_interface_port_name = port_name.split('lrp-')[1]
        return self.get_evpn_info_from_port_name(router_interface_port_name)

    def get_ip_from_port_peer(self, port):
        peer_name = port.options['peer']
        peer_port = self._get_port_by_name(peer_name)
        return peer_port.mac[0].split(' ')[1]

    def get_evpn_info_from_port(self, port):
        return self.get_evpn_info(port)

    def get_evpn_info_from_port_name(self, port_name):
        port = self._get_port_by_name(port_name)
        return self.get_evpn_info(port)

    def get_evpn_info(self, port):
        try:
            evpn_info = {
                'vni': int(port.external_ids[
                    constants.OVN_EVPN_VNI_EXT_ID_KEY]),
                'bgp_as': int(port.external_ids[
                    constants.OVN_EVPN_AS_EXT_ID_KEY])}
        except KeyError:
            return {}
        return evpn_info

    def get_port_if_local_chassis(self, port_name, chassis):
        port = self._get_port_by_name(port_name)
        if port.chassis[0].name == chassis:
            return port
        return None
