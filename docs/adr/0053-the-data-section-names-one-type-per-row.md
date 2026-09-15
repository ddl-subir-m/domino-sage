---
status: accepted
extends: ADR-0025 (the app section groups by relationship, never by type — the same move, one scope
  over), ADR-0026 (the glossary holds names and words, and only names owe a key), ADR-0014 (the
  overlay renames prose, not identifiers), ADR-0035 (the panel is the Project's one list)
---

# The Data section groups by shape, and says reach on the row

Three labels stood over two things. The [[Resource Browser]] drew a `Data` group holding `Datasets`
and `Data Sources`; Browse Domino's sidebar offered `Data`, `Datasets` and `Data Sources` as three
peers, the first one's count being the sum of the next two. Nothing in `CONTEXT.md` said what `Data`
was, because it was not a decision — it arrived in #164 as somewhere for a group's add door to
land, and became vocabulary by sitting there.

The two words under it are Domino's own, and they are not the problem. The problem is that a person
reading them has to already know that one means *files held here* and the other means *tables
reached over a connection*, because the labels say neither.

## One section, and the type names the shape

There is one `Data` section. A row in it has a **type**, and the type names what shape the data is:
**File volume** for a Dataset, **Tabular** for a Data Source.

Shape is the axis that answers what a person came to the section for. It is also the axis that
survives contact with the rest of Domino: External Data Volumes and Domino Volumes for NetApp ONTAP
are file-shaped things reached over a connection, and Sage models neither today, but both land in
this section the day it does.

## Reach is the other axis, and it is a word on the row

A data thing has a second, independent property: whether the Project holds it, or whether it reaches
a system outside. A Data Source is read over a connection; a Dataset is mounted.

The tempting move — and the one this ADR exists to refuse — is a third type beside File volume and
Tabular, called *Data connection*. It cannot work, because it is not on the same axis. A Data Source
is Tabular AND reached over a connection, so it would qualify for two of the three types with
nothing saying which one it gets; and the cell the set still cannot name — file-shaped, reached over
a connection — is the exact one Domino's own volume products occupy.

So reach is said on the row, in one word, **connected**, and only where it is true. Silence means
the Project holds the thing. Marking that case too would put a word on every data row in order to
distinguish nothing.

## The shapes are words; the things keep their names

`File volume`, `Tabular` and `connected` are `_Kind_: word` under
[ADR-0026](0026-the-glossary-holds-names-and-words-and-only-names-owe-a-key.md). They describe a
shape and a reach, so there is nothing for a partner to rename them TO — a pack that calls a Dataset
a *Collection* has not stopped it from holding files.

That is only safe because the row keeps the Domino noun. The rail's subhead used to be the one place
a panel row said whether it was a Dataset or a Data Source; grouping by shape takes that away, so
the noun moved onto the row, where `SW.util.labelFor` reads it from the pack exactly as before. The
catalog row already carried it. A person therefore reads the shape in the heading and the thing on
the row, and a pack still renames the half it owns
([ADR-0014](0014-the-overlay-renames-prose-not-identifiers.md)).

`volume` on its own is not available for this, and the two words are not a flourish. Domino sells a
product called Volumes, and in this codebase *the project volume* is the Project's own mounted
directory. `Dataset`'s `_Avoid_` list said `volume` before this change and still does; what it now
permits is the two-word type label, over a row that says `Dataset` beside it.

## Both subheads are drawn whenever their rows are

The panel named a subgroup only when a sibling subgroup also had rows. Under shape labels that rule
produces a Project holding only Datasets whose rows read `Data`, beside a Project holding both whose
identical rows read `Data / File volume`. Shape is a fact about the rows and not about what else is
in the section, so the Data group names its subheads unconditionally.

## The sidebar nests rather than flattens

Browse Domino keeps both shapes as filters — a person who wants only tabular things wants that
filter — but as children of `Data` rather than peers of it. Three peers whose first count is the sum
of the other two reads as a third kind of thing, which is how `Data` came to look like one.

## Uploads stay their own section

A file a person drops into a Conversation is not Project data. No app can read one until it crosses
into a writable Dataset and becomes an Attachment
([ADR-0023](0023-an-upload-crosses-by-becoming-an-attachment.md)), and it is removed from the list
that owns it and no other. Folding `Files` into `Data` would group it with things that are already
readable and already bindable, which is the one thing an Upload is not.

Rejected: **a third type, `Data connection`.** Reach on the type axis; it double-claims a Data
Source and still cannot name a file-shaped connection.
Rejected: **`Datasets` and `Data Sources` as the section's two subheads.** The state this replaces;
the labels name the things and say nothing about them, and `Data` above them is then an undefined
third word.
Rejected: **`volume` as the one-word type.** Taken twice over, once by Domino and once by this
codebase.
Rejected: **shape as a pack-renameable name.** A pack renames what a thing is called, not what shape
it is; a key here would be a key with nothing to put in it.
Rejected: **folding `Files` into `Data`.** ADR-0023's crossing is the whole distinction.
