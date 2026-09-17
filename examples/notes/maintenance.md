Status: active

# Synthetic maintenance notes

This collection is fictional and contains no operational infrastructure.

## Quartz rotation

The quartz rotation procedure keeps two credential slots. Populate the inactive
slot, observe the synthetic health signal, and switch only after human approval.

## Rollback

The rollback marker is `quartz-example-previous-slot`. An error cancels the
switch rather than reporting successful rotation.
