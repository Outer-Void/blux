import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location("preflight",Path(__file__).resolve().parents[1]/"tools/preflight.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.row=dict(source_id="fixture",origin="synthetic fixture",revision="1",license="CC0-1.0",permission_evidence="test only",source_type="synthetic",family_id="example-family",split="holdout",data_class="synthetic",eligibility="approved",deduplication_state="cleared",contamination_state="cleared",text="synthetic example",content_sha256=hashlib.sha256(b"synthetic example").hexdigest())
        self.lock={"version":1,"families":{},"digests":{}}

    def test_family_and_digest_cannot_be_reassigned(self):
        lock=m.validate([self.row],self.lock)
        for changes in [{"split":"train"},{"family_id":"renamed","split":"train"}]:
            with self.assertRaises(ValueError): m.validate([dict(self.row,**changes)],lock)
        self.assertEqual(self.lock,{"version":1,"families":{},"digests":{}})

    def test_private_reasoning_uncleared_and_duplicates_rejected(self):
        for changes in [{"data_class":"private"},{"source_type":"provider_hidden_reasoning"},{"eligibility":"pending"},{"contamination_state":"pending"},{"content_sha256":"a"*64}]:
            with self.assertRaises(ValueError): m.validate([dict(self.row,**changes)],self.lock)
        with self.assertRaises(ValueError): m.validate([self.row,copy.deepcopy(self.row)],self.lock)

    def test_valid_record_is_idempotent(self):
        result=m.validate([self.row],self.lock)
        self.assertEqual(m.validate([self.row],result),result)
