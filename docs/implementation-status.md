# Implementation evidence and remaining validation

The XML/JSON parsers accept this repository's normalized source schemas.
They do **not** accept arbitrary native SharePoint Designer XAML, Nintex NWF,
or tenant exports. A native-export adapter remains open until representative,
sanitized exports and expected behavior are available. No vendor compatibility
claim is implied by the included synthetic samples.

## Solution preparation

`python -m wma prepare-solution definition.json --template exported-solution --workflow Workflows/name-guid.json --out prepared-solution --bindings bindings.json`

The template must be an existing PAC-unpacked XML-format solution containing
the selected cloud flow and its genuine connection references. A bindings JSON
maps emitted connector names to reference keys, e.g.
`{"shared_office365": "shared_office365_1"}`.

The command copies the template, preserves solution identity and references,
checks local static rules, rejects TODO actions and missing/incompatible
connection bindings, and replaces only the selected definition. Use a new
output folder. This format-specific preparation helper does not construct a
valid solution from an arbitrary JSON file.

Optional `--zipfile reviewed.zip` invokes an installed Microsoft PAC CLI to pack
the prepared copy. Tests mock PAC; no real pack or Dataverse import has been
performed in this implementation session.

Before importing, review trigger configuration, site/list IDs, expressions,
environment variables, connector permissions, licensing, DLP policies and
connection references in a disposable environment. Create deployment settings
with PAC and import the reviewed solution explicitly:

```powershell
pac solution create-settings --solution-zip reviewed.zip --settings-file settings.json
# Populate genuine target environment / connection values in settings.json first.
pac solution import --path reviewed.zip --environment https://YOUR-ORG.crm.dynamics.com --settings-file settings.json
```

This tool never authenticates or imports automatically. Successful static checks
or mocked simulation are not evidence of tenant execution or behavioral
equivalence. Retain import logs and compare target run outputs with real legacy
workflow cases before describing a migration as validated.

References:
- [Microsoft PAC solution commands](https://learn.microsoft.com/power-platform/developer/cli/reference/solution)
- [Export solution cloud flows](https://learn.microsoft.com/power-automate/export-flow-solution)
- [Microsoft example of cloud-flow client data](https://github.com/MicrosoftDocs/power-automate-docs/blob/main/articles/manage-flows-with-code.md)
