"""Independent acceptance rejects altered observations, even with pass labels."""
import base64,copy,unittest
from tools.tests.test_fingerprint_build_pipeline import candidate
from tools.tests.pixel_semantics_fixtures import input_preservation_fixture

def fixture():
    rows=[]
    for identity in candidate.INPUT_PRESERVATION_SCENARIOS:
        probe,extra=input_preservation_fixture(identity)
        rows.append({'id':identity,'probe':probe,'extra':{'input_preservation':extra}})
    return rows

class InputCandidateTests(unittest.TestCase):
    def test_complete_raw_evidence_has_all_mandatory_compatibility_checks(self):
        required=candidate.validate_application_inputs(fixture())
        self.assertEqual(len(required),285)
        self.assertIn(('canvas_shardx','input_mixed_untouched_half'),required)
        self.assertIn(('shard_surfaces','input_rendered_write_preserved'),required)

    def test_missing_or_changed_input_evidence_is_rejected(self):
        for identity in candidate.INPUT_PRESERVATION_SCENARIOS:
            for mutate in [lambda row:row['extra'].pop('input_preservation'),
                           lambda row:row['extra']['input_preservation'].update(version=True),
                           lambda row:row['extra']['input_preservation']['canvas'].pop('offscreen'),
                           lambda row:row['extra']['input_preservation']['audio']['input'].update(view=base64.b64encode(bytes(512)).decode()),
                           lambda row:row['extra']['input_preservation']['audio']['renderedEdit'].update(sameView=False),
                           lambda row:row['extra']['input_preservation']['canvas']['display-p3']['attributes'].update(colorSpace='srgb')]:
                rows=fixture();mutate(next(row for row in rows if row['id']==identity))
                with self.subTest(identity=identity),self.assertRaises(ValueError):candidate.validate_application_inputs(rows)

    def test_disabling_rendered_noise_cannot_pass_input_preservation(self):
        for identity,surface in [('canvas_shardx','canvas'),('hardware_cloak','canvas'),('legacy_seeded','canvas'),('shard_surfaces','audio')]:
            rows=fixture();baseline=rows[0]['probe'][surface]['rendered']
            next(row for row in rows if row['id']==identity)['probe'][surface]['rendered']=baseline
            with self.subTest(identity=identity),self.assertRaises(ValueError):candidate.validate_application_inputs(rows)

    def test_sample_changes_outside_existing_low_bits_are_rejected(self):
        rows=fixture();audio=next(row for row in rows if row['id']=='shard_surfaces')['probe']['audio']
        raw=bytearray(base64.b64decode(audio['rendered']));raw[3]^=0x80;audio['rendered']=base64.b64encode(raw).decode()
        with self.assertRaises(ValueError):candidate.validate_application_inputs(rows)

if __name__=='__main__':unittest.main()
