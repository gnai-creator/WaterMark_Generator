# Rotation verification

Pin `public/identity.json`, verify each signed manifest in ascending `sequence`,
and require each `previous_manifest_hash` to equal the full prior manifest hash.
Reject rollback, gaps, forks, invalid signatures, and identity-key changes.
