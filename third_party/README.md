# Vendored MuseTalk

Source: https://github.com/TMElyralab/MuseTalk

Commit: `0a89dec45a0192b824e3cf4daf96c239440c5ed8`.

`MuseTalk/` contains unchanged upstream `musetalk`, `scripts`, `configs`, requirements,
download scripts, README and LICENSE. Demo media, generated results, repository metadata
and weights are excluded. File SHA-256 values are recorded in `MuseTalk.source.json`.
Integration code lives in `lipsync_service/`; no inference changes are hidden in the vendor tree.

This commit was obtained from upstream during integration. It has NOT yet been
compared with the user's successful server installation. Model version `v15` does
not identify a unique source commit. Capture the working server records with
`scripts/capture_baseline.sh` before calling this a reproduced deployment.

`deployment/models.json` pins the four Hugging Face repositories by commit. Large
files have published LFS SHA-256 values; small config files use their Git blob IDs.
BiSeNet uses the original Google Drive URL and the known 79999_iter.pth SHA-256.
ResNet18 and S3FD use the upstream URLs and SHA-256 prefixes embedded in those URLs.
The latter prefixes are shorter integrity checks, not full-length SHA-256 pins.
All models are fetched separately at deployment time; see upstream licenses and
model cards for the corresponding assets.
