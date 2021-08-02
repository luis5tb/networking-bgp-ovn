from unittest import mock

from networking_bgp_ovn.tests import base as test_base


class TestAgentCmd(test_base.TestCase):
    @mock.patch('networking_bgp_ovn.agent.start')
    def test_start(self, m_start):
        from networking_bgp_ovn.cmd import agent  # To make it import a mock.
        agent.start()

        m_start.assert_called()
