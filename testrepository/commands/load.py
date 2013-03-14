#
# Copyright (c) 2009 Testrepository Contributors
#
# Licensed under either the Apache License, Version 2.0 or the BSD 3-clause
# license at the users choice. A copy of both licenses are available in the
# project source as Apache-2.0 and BSD. You may not use this file except in
# compliance with one of these two licences.
#
# Unless required by applicable law or agreed to in writing, software
# distributed under these licenses is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
# license you chose for the specific language governing permissions and
# limitations under that license.

"""Load data into a repository."""

from functools import partial
from operator import methodcaller
import optparse

from extras import try_import
v2_avail = try_import('subunit.ByteStreamToStreamResult')

import subunit.test_results
import testtools
from testtools import ConcurrentTestSuite, MultiTestResult, Tagger

from testrepository.arguments.path import ExistingPathArgument
from testrepository.commands import Command
from testrepository.repository import RepositoryNotFound
from testrepository.testcommand import TestCommand


def _wrap_result(result, thread_number):
    worker_id = 'worker-%s' % thread_number
    tags_to_add = set([worker_id])
    tags_to_remove = set()
    return subunit.test_results.AutoTimingTestResultDecorator(
        Tagger(result, tags_to_add, tags_to_remove))


class load(Command):
    """Load a subunit stream into a repository.

    Failing tests are shown on the console and a summary of the stream is
    printed at the end.

    Unless the stream is a partial stream, any existing failures are discarded.
    """

    input_streams = ['subunit+']

    args = [ExistingPathArgument('streams', min=0, max=None)]
    options = [
        optparse.Option("--partial", action="store_true",
            default=False, help="The stream being loaded was a partial run."),
        optparse.Option(
            "--force-init", action="store_true",
            default=False,
            help="Initialise the repository if it does not exist already"),
        optparse.Option("--subunit", action="store_true",
            default=False, help="Display results in subunit format."),
        optparse.Option("--full-results", action="store_true",
            default=False,
            help="No-op - deprecated and kept only for backwards compat."),
        ]
    # Can be assigned to to inject a custom command factory.
    command_factory = TestCommand

    def run(self):
        path = self.ui.here
        try:
            repo = self.repository_factory.open(path)
        except RepositoryNotFound:
            if self.ui.options.force_init:
                repo = self.repository_factory.initialise(path)
            else:
                raise
        testcommand = self.command_factory(self.ui, repo)
        # Not a full implementation of TestCase, but we only need to iterate
        # back to it. Needs to be a callable - its a head fake for
        # testsuite.add.
        # XXX: Be nice if we could declare that the argument, which is a path,
        # is to be an input stream.
        if self.ui.arguments.get('streams'):
            opener = partial(open, mode='rb')
            cases = lambda:map(opener, self.ui.arguments['streams'])
        else:
            cases = lambda:self.ui.iter_streams('subunit')
        def make_tests(suite):
            streams = list(suite)[0]
            for pos, stream in enumerate(streams()):
                if v2_avail:
                    # Calls StreamResult API.
                    case = subunit.ByteStreamToStreamResult(
                        stream, non_subunit_name='stdout')
                else:
                    # Calls TestResult API.
                    case = subunit.ProtocolTestCase(stream)
                    case = testtools.DecorateTestCaseResult(
                        case,
                        testtools.ExtendedToStreamDecorator,
                        methodcaller('startTestRun'),
                        methodcaller('stopTestRun'))
                case = testtools.DecorateTestCaseResult(case,
                    lambda result:testtools.StreamTagger(
                        [result], add=['worker-%d' % pos]))
                yield (case, str(pos))
        case = testtools.ConcurrentStreamTestSuite(cases, make_tests)
        # One copy of the stream to repository storage
        inserter = repo.get_inserter(partial=self.ui.options.partial)
        # One copy of the stream to the UI layer after performing global
        # filters.
        try:
            previous_run = repo.get_latest_run()
        except KeyError:
            previous_run = None
        output_result = self.ui.make_result(
            lambda: inserter._run_id, testcommand, previous_run=previous_run)
        result = testtools.CopyStreamResult([
            testtools.StreamToExtendedDecorator(inserter), output_result])
        result.startTestRun()
        try:
            case.run(result)
        finally:
            result.stopTestRun()
        if not output_result.wasSuccessful():
            return 1
        else:
            return 0
