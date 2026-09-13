# Triage Labels

The skills speak in terms of five canonical triage roles, and this repo adds one of its own. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |
| _(none — ours)_            | `later`              | Real, correctly filed, no live symptom   |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

`later` is the one role with no counterpart in the skills. It exists because a review that
finds real things produces more tickets than anyone can hold, and `ready-for-agent` then stops
meaning "pick this up" and starts meaning "filed". A `later` issue is not lesser work — it is
work with no live symptom, to be picked up when its area is next opened for another reason.

Edit the right-hand column to match whatever vocabulary you actually use.
