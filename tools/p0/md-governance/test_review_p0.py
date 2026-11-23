"""Diagnostic states and retry limits must not issue publication credentials."""
from contextlib import redirect_stderr
from io import StringIO
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import md_backend
import md_context
import md_gate
import md_semantic
import md_store
from md_progress import ReviewFailure, run_reviewer
from test_support import verdict


class ReviewTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.target = self.root / 'AGENTS.md'
        self.draft = self.root / 'candidate.stage'
        self.request = self.root / 'request.txt'
        self.target.write_text('# Project rules\n\nPreserve existing work before editing.\n')
        self.draft.write_text(self.target.read_text() + '\nRun the project checks after editing runtime code.\n')
        self.request.write_text('Add verification for runtime code changes.\n')
        for module, attr, value in ((md_store, 'STATE', self.root / 'state'),
                                    (md_gate, 'GLOBAL', self.root / 'unused/AGENTS.md')):
            fixture = patch.object(module, attr, value)
            fixture.start()
            self.addCleanup(fixture.stop)

    def run_review(self, **options):
        return md_semantic.run(self.target,self.draft,self.request,**options)

    def response(self):
        return verdict(md_context.build(self.target,self.draft,self.request)[2])

    def test_timeout_feedback_is_reused_until_explicit_retry(self):
        expired = ReviewFailure('timeout', 'Timed out. See failure-trace.json.')
        approved = (self.response(), {'provider': 'test fixture'})
        with patch('md_backend.invoke', side_effect=[expired, approved]) as backend:
            first = self.run_review()
            again = self.run_review()
            self.assertEqual(first['reason'], 'timeout')
            self.assertNotIn('review', first)
            self.assertTrue(again['reused'])
            self.assertEqual(backend.call_count, 1)
            renamed = self.draft.with_name('same-content.stage')
            self.draft.rename(renamed)
            self.draft = renamed
            self.assertTrue(self.run_review()['reused'])
            self.assertEqual(backend.call_count, 1)
            after = self.run_review(retry=True)
            self.assertEqual(after['decision'], 'pass')
            self.assertEqual(backend.call_count, 2)
        self.assertNotIn('Run the project checks', self.target.read_text())

    def test_connection_failure_retries_once(self):
        failure = ReviewFailure('transport', 'Network connection ended.')
        with patch('md_backend.invoke', side_effect=failure) as backend:
            result = self.run_review()
            self.assertEqual(result['reason'], 'transport')
            self.assertNotIn('review', result)
            self.assertEqual(backend.call_count, 2)

    def test_proposed_clause_omission_and_content_rejection_are_distinct(self):
        answer = self.response()
        answer['clauses'] = []
        with patch('md_backend.invoke', return_value=(answer, {})):
            result = self.run_review()
            self.assertEqual(result['reason'], 'invalid_response')
            self.assertIn('candidate lines', result['error'])
        rejection = self.response()
        rejection['dimensions']['scope'].update(status='revise', suggestion='Limit it to runtime changes.')
        rejection['decision'] = 'revise'
        with patch('md_backend.invoke', return_value=(rejection, {})) as backend:
            self.assertEqual(self.run_review(retry=True)['decision'], 'revise')
            self.assertTrue(self.run_review(retry=True)['reused'])
            self.assertEqual(backend.call_count, 1)

    def test_response_schema_only_allows_added_clause_ids(self):
        packet = md_context.build(self.target, self.draft, self.request)[2]
        clause_ids = [row['id'] for row in packet['added']]
        schema = md_backend.review_schema(clause_ids)
        allowed = schema['properties']['clauses']['items']['properties']['id']['enum']
        self.assertEqual(allowed, clause_ids)
        answer = self.response()
        answer['clauses'].append(dict(answer['clauses'][0], id='unrelated-id'))
        with patch('md_backend.invoke', return_value=(answer, {})):
            result = self.run_review()
            self.assertEqual(result['reason'], 'invalid_response')
            self.assertIn('Unexpected clause IDs: 1', result['error'])

    def test_heartbeat_and_deadline_do_not_echo_candidate(self):
        log = StringIO()
        child = [sys.executable, '-c', 'import time; time.sleep(5)']
        with redirect_stderr(log):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_reviewer(child, 'private candidate fixture', dict(os.environ), self.root,
                             timeout=0.25, interval=0.05)
        self.assertIn('still running', log.getvalue())
        self.assertIn(str(self.root), log.getvalue())
        self.assertNotIn('private candidate fixture', log.getvalue())


if __name__ == '__main__':
    unittest.main()
