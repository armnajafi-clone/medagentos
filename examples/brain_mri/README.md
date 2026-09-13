# Brain MRI reference example

Runs the eight-step reference workflow on a synthetic volume descriptor. No
model download, no GPU, no services — the segmentation adapter derives a stable
pseudo-result from the input digest.

```bash
PYTHONPATH=src:plugins python -m medagentos.cli run brain_mri_reference \
    --input examples/brain_mri/case.json --approve dr-reviewer
```

Without `--approve` the run stops at the human review gate and reports
`awaiting_approval`, which is the point: no report is final without a reviewer.

Useful flags:

| Flag | Effect |
|---|---|
| `--approve NAME` | approve the review gate as `NAME` and resume |
| `--trace` | print the full execution trace |
| `--report` | print the generated report |
| `--json` | machine-readable output |

`case.json` contains no patient data. `volume_digest` stands in for the content
digest of a real volume; the synthetic adapter consumes the digest rather than
voxels, which is what keeps the example reproducible everywhere.
