# An assertion must fail when its evidence is missing

A test that passes because it found nothing to check is worse than no test. It reports green, it
reports green forever, and the day the thing it guarded regressed is the day nobody heard anything.
This is the one rule in this directory that is not about what to assert but about when an assertion
is allowed to pass.

## The rule

**Every assertion over a collection, a window, or a filtered subset carries a second assertion
naming what must be PRESENT.** The first says what the evidence must look like. The second says
there was evidence.

The repo idiom is one line:

```python
assert metas and all("size not measured" in m for m in metas)
assert record and all("menu_folder" not in e for e in record)
```

Where the count is known, the count is better, because it also catches a subset:

```python
assert len(project.status()["attached"]) == 8
assert all(not e["menu_folder"] for e in project.status()["attached"])
```

A positive assertion over the same subject counts as the pin. `assert "x" in tracked` beside
`assert "y" not in tracked` already says the listing is not empty, and needs nothing added.

## The three spellings

The defect has no single shape to grep for. It is the same bug each time:

| Spelling | Passes when |
|---|---|
| `all(d["ok"] for d in doors)` | `doors` is empty |
| `assert X not in window` | `window` is the wrong window, or `""` |
| `for x in xs: if cond: assert ...` | no `x` meets `cond` |

`some()` over an unpopulated list, a `None` guard that skips the body, a `.find()` that returned
`-1` and was read as "absent" — all the same. Ask of every assertion: *what would this do if the
thing it is about were not there at all?*

## Both directions are the same root

The sign can flip. A check can also FAIL when the evidence is present — a `grep` against a path
that does not exist returns nothing, and "no matches" is read as "the symbol is absent". That is the
same defect with the opposite sign, and it is the safe direction to be wrong in, because it is loud.

So the fix is not only "assert that something must be present". It is: **make the no-evidence case
distinguishable from both answers.** A flag that says the window was located is worth more than a
flag that says the window said no.

## Windows over a source body

Several tests here read a file and assert over one region of it. Those windows are
**delimiter-bounded, never character-counted.**

```python
start = src.index("async function loadAppHistory(")
assert "conversationView" not in src[start : src.index("\n  }\n", start)]
```

`str.index` is the other half of that: it raises when the delimiter is gone, so a window that cannot
be found is a failure rather than a window of whatever happened to be there. `str.find` returns `-1`
and slices from the end. Prefer `index`.

```js
const at = src.search(DEF(name));
const found = at !== -1;                    // the window was located
const rest = src.slice(at + 1);
const next = rest.search(DEF('[a-zA-Z_$][\\w$]*'));
const body = next === -1 ? rest : rest.slice(0, next);   // to the next method
```

`src.slice(at, at + 3000)` is the shape that failed: a comment added inside one door pushed the call
being asserted on past the end of the window, `indexOf` returned `-1`, and the ordering assertion
retired itself in silence. Prose grows. Character counts do not grow with it, and nothing announces
the moment one expires (#267).

Anchor on the DEFINITION, not on the first call site — `store.newThread()` is called from inside
`store.js`, so a bare-name search finds a call and reads the wrong body. And carry the `found` flag
out to the assertion: with `at === -1`, `slice(0)` scans the whole file and every flag answers about
some other body, which reads exactly like a right answer.

## Where this came from

#264 landed a comment that silently disarmed an assertion in
`js/sensitivity_session_lock_harness.mjs`. #267 swept the suite for the rest of the class. The
`reads` field in that harness — a flag whose only job is to say the evidence was there — is the
pattern to copy.
