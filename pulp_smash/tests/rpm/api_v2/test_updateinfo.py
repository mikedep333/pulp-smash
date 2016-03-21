# coding=utf-8
"""Test updateinfo XML generated by yum distributor."""
from __future__ import unicode_literals

from pulp_smash import utils, api, selectors
from pulp_smash.compat import urljoin
from pulp_smash.constants import CONTENT_UPLOAD_PATH, REPOSITORY_PATH
from pulp_smash.tests.rpm.api_v2.utils import BaseRepoMDTestCase, gen_repo, \
    gen_distributor


class UpdateInfoTestCase(BaseRepoMDTestCase):
    """Ensures updateinfo.xml is generated with appropriate content."""

    @staticmethod
    def get_spawned_tasks(client, async_response):
        """Fetch, return task for every spawned task in the given response."""
        return [client.get(t['_href'])
                for t in async_response['spawned_tasks']]

    @classmethod
    def import_erratum(cls, client, repo, erratum):
        """Import a single erratum to a repo.

        Returns the tasks created as a result of the import. There's expected
        to be only one task, but that's not verified here.
        """
        upload_request = client.post(CONTENT_UPLOAD_PATH)
        import_args = {'upload_id': upload_request['upload_id'],
                       'unit_type_id': 'erratum',
                       'unit_key': {'id': erratum['id']},
                       'unit_metadata': erratum}

        import_response = client.post(
            urljoin(repo['_href'], 'actions/import_upload/'),
            import_args)
        return cls.get_spawned_tasks(client, import_response)

    @classmethod
    def import_updates(cls, client, repo):
        """Import a set of updates, later used within test methods."""
        cls.errata['typical'] = {
            'id': utils.uuid4(),
            'description': (
                'This sample description contains some non-ASCII characters '
                ', such as: 汉堡™, and also contains a long line which some '
                'systems may be tempted to wrap.  It will be tested to see '
                'if the string survives a round-trip through the API and '
                'back out of the yum distributor as XML without any '
                'modification.'),
            'pkglist': [
                {'name': 'pkglist-name',
                 'packages': [
                     {
                         'arch': 'i686',
                         'epoch': '0',
                         'filename': 'libpfm-4.4.0-9.el7.i686.rpm',
                         'name': 'libpfm',
                         'release': '9.el7',
                         'src': 'libpfm-4.4.0-9.el7.src.rpm',
                         'sum': [
                             'sha256',
                             ('ca42a0d97fd99a195b30f9256823a46c94f632c126ab4f'
                              'bbdd7e127641f30ee4')
                         ],
                         'version': '4.4.0'
                     }
                 ]}
            ],
            'references': [
                {
                    'href': 'https://example.com/errata/PULP-2017-1234.html',
                    'id': 'PULP-2017:1234',
                    'title': 'PULP-2017:1234',
                    'type': 'self'
                },
            ],
            'type': 'PULP',
            'title': 'sample title',
            'solution': 'sample solution',
            'status': 'final',
            'version': '6',  # intentionally string, not int,
            'issued': '2015-03-05 05:42:53 UTC',
        }

        cls.errata['no_pkglist'] = {
            'id': utils.uuid4(),
            'description': 'this unit has no packages',
            'type': 'PULP',
            'title': 'no pkglist',
            'solution': 'solution for no pkglist',
            'status': 'final',
            'version': '9',
            'issued': '2015-04-05 05:42:53 UTC',
        }

        for (errata_key, errata_unit) in cls.errata.items():
            import_key = 'import_%s' % errata_key
            cls.tasks[import_key] = cls.import_erratum(
                client, repo, errata_unit)

    @classmethod
    def setUpClass(cls):
        """Publish a yum repo containing some updates."""
        super(UpdateInfoTestCase, cls).setUpClass()

        cls.tasks = {}
        cls.errata = {}

        client = api.Client(cls.cfg, api.json_handler)

        # Create a repository for use by the test.
        repo = client.post(REPOSITORY_PATH, gen_repo())
        cls.resources.add(repo['_href'])

        # add yum distributor to the repo
        distribute = client.post(
            urljoin(repo['_href'], 'distributors/'),
            gen_distributor())

        # import some errata
        cls.import_updates(client, repo)

        # ask for it to be published
        client.post(
            urljoin(repo['_href'], 'actions/publish/'),
            {'id': distribute['id']})

        repo_url = urljoin('/pulp/repos/',
                           distribute['config']['relative_url'])
        cls.updateinfo_tree = cls.get_repodata_xml(repo_url, 'updateinfo')

    def assert_task_successful(self, task, msg):
        """Assert that a task completed successfully.

        This is more extensive than the checks built-in to the api Client,
        because it also checks the success_flag on the task's result,
        if present.
        """
        self.assertEqual('finished', task['state'], msg)
        if 'result' in task:
            result = task['result']
            self.assertTrue(result['success_flag'],
                            '%s result is not successful: %s' % (
                                msg, result['details']))

    @property
    def update_nodes_by_id(self):
        """dict of updateinfo.xml update notes, grouped by update id."""
        out = {}
        updates = self.updateinfo_tree.findall('update')
        for update in updates:
            id_text = self.get_single_element(update, 'id').text
            self.assertNotIn(id_text, out, 'duplicate id %s' % id_text)
            out[id_text] = update
        return out

    def test_unit_import_succeeded(self):
        """Test that the unit import tasks succeeded.

        Most other tests have no meaning if this test has failed.
        """
        self.assertTrue(self.errata)
        for key in self.errata:
            msg = '%s task' % type
            tasks = self.tasks['import_%s' % key]
            self.assertEqual(1, len(tasks), msg)
            self.assert_task_successful(tasks[0], msg)

    def test_updateinfo_sanity(self):
        """Test basic structure of the generated updateinfo.xml file."""
        # should have one top-level <updates>
        self.assertEqual('updates', self.updateinfo_tree.getroot().tag)

        update_elems = self.updateinfo_tree.findall('update')

        # should have one <update> per imported unit
        self.assertEqual(len(self.errata), len(update_elems))

    def test_updateinfo_description(self):
        """Test that description survives a round trip through API to XML.

        In particular, tests a description with non-ASCII characters and
        long lines.
        """
        erratum = self.errata['typical']

        typical_id = erratum['id']
        update_node = self.update_nodes_by_id[typical_id]
        description_node = self.get_single_element(update_node, 'description')
        description_text = description_node.text

        # should survive a round-trip exactly
        self.assertEqual(erratum['description'], description_text)

    def test_absent_reboot_suggested(self):
        """Test that no reboot_suggested element exists if omitted in unit.

        https://pulp.plan.io/issues/1782
        """
        erratum = self.errata['typical']
        update_node = self.update_nodes_by_id[erratum['id']]
        reboot_suggested = update_node.findall('reboot_suggested')

        if selectors.bug_is_untestable(1782):
            self.skipTest('https://pulp.plan.io/issues/1782')

        # Because the unit did not specify any reboot_suggested, it should
        # also be omitted from the XML.
        self.assertFalse(reboot_suggested,
                         ('reboot_suggested element(s) were found where none '
                          'were expected'))