# Sherpa RPA Deterministic XML Skill — Master Context

Version: 0.3-draft  
Status: working master context for agent testing  
Purpose: single-file canonical context for continuing design and implementation of a deterministic XML generation skill for Sherpa RPA.

---

## 1. Project Goal

Build a skill that generates deterministic `.process` XML for Sherpa RPA from an intermediate representation (IR), validates the result structurally and semantically, and avoids unsafe or non-confirmed XML synthesis.

Primary requirements:

- Deterministic XML output for identical IR input.
- Separation between:
  - IR model,
  - XML compiler,
  - structural validator,
  - semantic validator.
- Safe fallback behavior when a required Sherpa block is not yet confirmed.
- Incremental extension from real golden reference `.process` files.

---

## 2. Current Scope

Currently confirmed or partially confirmed areas:

- Process graph basics.
- Placeholder block `ToDo`.
- Variable declarations.
- `Assign` block.
- `Condition` / `IfElse`.
- `While`.
- Types:
  - `Number`
  - `Logical`
  - `Text`
  - `SecureString`
  - `Hashtable`
  - `Dictionary`
  - `List`
  - `DataTable`
- Collection blocks for:
  - hashtable/dictionary
  - list
  - data table
- `Text` initial value serialization.
- `DataTable` initial value serialization for a confirmed subset.

Not yet fully confirmed:

- Full nested object serialization in table cells.
- Full set of `While` filled-condition golden samples.
- Full expression grammar coverage from golden files.
- Full set of rare numeric column types for `DataTable`.
- Nested `Hashtable`/`DataTable` as cell values.
- Deterministic serializer implementation code.

---

## 3. Design Principles

### 3.1 Determinism

For identical IR input, the generated XML must be byte-stable, excluding intentionally variable identifiers if any remain. Preferred target is fully deterministic output including IDs.

Determinism applies to:

- element ordering
- attribute ordering
- connector ordering
- UUID generation
- block ordering
- serialized text values
- serialized table values

### 3.2 Confirmed-over-Invented

The generator must not invent Sherpa XML structures that are not confirmed by golden references.

Policy:

- Confirmed structure → generate directly.
- Partially confirmed structure → generate only in explicitly allowed draft mode.
- Unconfirmed structure → use controlled fallback (`ToDo`) or fail validation.

### 3.3 Split Validation

Validation is split into two layers:

#### Structural validation
Checks XML shape and graph relations.

#### Semantic validation
Checks expression meaning, type compatibility, deterministic serialization, and higher-level process consistency.

### 3.4 Safe Fallback

If there is no confirmed block for an intended action, generation must not silently substitute a "similar" action. Instead it must create a controlled placeholder block and register unresolved intent.

---

## 4. Operating Modes

```yaml
modes:
  draft:
    allows_todo: true
    allows_partial_support: true
    blocks_release_on_todo: false

  release:
    allows_todo: false
    allows_partial_support: false
    blocks_release_on_todo: true

  strict:
    release_rules: true
    forbids_implicit_truthiness: true
    forbids_unconfirmed_type_coercions: true
    requires_confirmed_patterns_only: true
```

---

## 5. Architecture

```text
User Intent / Source IR
        ↓
IR Builder
        ↓
Type Resolution
        ↓
Expression Compiler
        ↓
Block Compiler
        ↓
XML Serializer
        ↓
Structural Validation
        ↓
Semantic Validation
        ↓
Determinism Check
        ↓
.process output
```

Recommended package structure:

```text
sherpa_xml/
├── ir.py
├── compiler.py
├── serializer.py
├── validator.py
├── expressions.py
├── identifiers.py
├── layout.py
├── block_catalog.py
├── type_catalog.py
├── profiles.py
└── types/
    ├── text.py
    ├── secure_string.py
    ├── collections.py
    ├── data_table.py
    └── primitives.py
```

---

## 6. Intermediate Representation (IR)

### 6.1 Core IR

```yaml
process:
  name: Example Process
  mode: draft|release|strict

variables: []
nodes: []
edges: []
external_variables: []
metadata: {}
```

### 6.2 Variables

```yaml
variables:
  - key: var_1
    name: Counter
    sherpa_type: Number
    scope: local
    initial:
      kind: number_literal
      value: 0
```

### 6.3 Nodes

```yaml
nodes:
  - key: assign_1
    type: Assign
    assignments: []

  - key: cond_1
    type: Condition
    condition: {}
    branches: {}

  - key: while_1
    type: WhileLoop
    condition: {}
    body: {}
    next: after_loop

  - key: todo_1
    type: ToDo
```

### 6.4 Edges

```yaml
edges:
  - from: cond_1
    port: Yes
    to: node_a

  - from: cond_1
    port: No
    to: node_b
```

---

## 7. Identifier Policy

### 7.1 Deterministic IDs

All generated IDs should be deterministic, preferably UUIDv5 from stable namespaces.

Recommended seed components:

- process name
- node key
- logical block type
- stable path within graph

Example:

```text
uuid5(NAMESPACE_PROCESS, "process-name::node-key::Assign")
```

### 7.2 Never Copy Golden IDs

Golden file block IDs are reference-only and must not be reused directly.

---

## 8. Layout Policy

Layout is not business logic, but deterministic generation should still stabilize it.

Recommended policy:

```yaml
layout:
  x_strategy: stable_incremental
  y_strategy: lane_based
  spacing:
    x: 240
    y: 140
```

Until a full layout engine exists, layout may be simplistic but must be deterministic.

---

## 9. Placeholder Block: ToDo

### 9.1 Meaning

`ToDo` is a controlled placeholder used when no confirmed Sherpa block exists for a requested action.

### 9.2 Behavior

It is a pass-through block:

- no real work
- forwards control via `Next`
- used to preserve graph completeness

### 9.3 IR

```yaml
- key: todo_1
  type: ToDo
  purpose: placeholder
  executable: false
  unresolved_action: "Requested action description"
  resolution:
    status: no_matching_block
```

### 9.4 Policy

- Allowed in `draft`.
- Forbidden in `release`.
- Must be listed in unresolved registry.

### 9.5 Unresolved Registry

```yaml
unresolved_actions:
  - node_key: todo_1
    requested_action: "Do something"
    reason: no_matching_block
    status: unresolved
```

---

## 10. Primitive and Core Variable Types

## 10.1 Number

Confirmed.

```yaml
Number:
  declaration_storage: plain string number
  examples:
    - "0"
    - "10"
```

## 10.2 Logical

Confirmed.

```yaml
Logical:
  declaration_storage:
    true: "True"
    false: "False"

  expression_storage:
    true: "$True"
    false: "$False"
```

Important: declaration and expression serialization differ.

## 10.3 Text

Confirmed for initial declaration and runtime assignment.

### Declaration

Initial value in `DiagramProperty` is not plain text. It is stored as:

- PowerShell string literal
- UTF-8 bytes
- wrapped in a binary string container
- encoded as Base64

### Runtime assignment

Runtime assignment through `Assign` uses ordinary PowerShell string literal syntax, e.g.:

```powershell
"Test"
```

### IR

```yaml
initial:
  kind: string_literal
  value: Test123
```

### Confirmed distinction

- declaration storage: Base64 binary container
- runtime expression storage: PowerShell string literal

## 10.4 SecureString

Confirmed.

### Declaration

Empty `SecureString` is stored as empty string in declaration.

### Runtime operations

Confirmed functions:

- `_tosecure(...)`
- `_fromsecure(...)`

### IR

```yaml
expression_kinds:
  secure_encode:
    sherpa_function: _tosecure
    arguments: 1
    inferred_result_type: SecureString

  secure_decode:
    sherpa_function: _fromsecure
    arguments: 1
    inferred_result_type: Text
```

---

## 11. Text Serialization Rules

### 11.1 Confirmed Model

For `DiagramProperty Type="Text"`:

```text
Base64(
  PREFIX
  + encode_7bit(len(UTF8(powerShellLiteral)))
  + UTF8(powerShellLiteral)
  + 0x0B
)
```

### 11.2 PowerShell literal examples

```yaml
text_literals:
  empty: '""'
  ascii: '"Test123"'
  unicode: '"Привет мир"'
  quotes: '"Он сказал `"Тест`""'
```

### 11.3 Newline handling

Distinguish contexts:

```yaml
text_contexts:
  powershell_literal:
    newline: "`n"

  datatable_diffgram_cell:
    newline: actual_line_feed
```

### 11.4 Validation Notes

Need checks for:

- Base64 validity
- binary header
- string record marker
- length correctness
- UTF-8 decoding
- valid PowerShell literal form

---

## 12. Collections: Hashtable and Dictionary

## 12.1 Runtime Types

```yaml
confirmed_runtime_types:
  Hashtable: System.Collections.Hashtable
  Dictionary: System.Collections.Generic.Dictionary[System.Object,System.Object]
```

Important correction:
`Dictionary` is not currently treated as confirmed ordered dictionary.

## 12.2 Shared Physical Blocks

Sherpa uses physical block names `Hashtable*` for both:

- `Hashtable`
- `Dictionary<Object,Object>`

So logical IR operations must be abstracted from physical block names.

### IR abstraction

```yaml
logical_operations:
  CollectionClear:
    sherpa_internal_name: HashtableClear
    accepts: [Hashtable, Dictionary]

  CollectionSetItem:
    sherpa_internal_name: HashtableAddItem
    accepts: [Hashtable, Dictionary]

  CollectionRemoveItem:
    sherpa_internal_name: HashtableRemoveItem
    accepts: [Hashtable, Dictionary]

  CollectionContainsKey:
    sherpa_internal_name: HashtableContainsKey
    accepts: [Hashtable, Dictionary]

  CollectionContainsValue:
    sherpa_internal_name: HashtableContainsValue
    accepts: [Hashtable, Dictionary]
```

## 12.3 Reference Format Difference

In collection blocks, variable reference is typically stored without `$`:

```text
DictSample
```

In expressions it uses:

```text
$DictSample
```

Compiler must distinguish:

```python
block_variable_ref(name)      -> name
expression_variable_ref(name) -> "$" + name
```

## 12.4 Order Policy

```yaml
collection_order:
  Hashtable:
    xml_generation: canonical_sort
    runtime_iteration: unspecified

  Dictionary:
    xml_generation: preserve_ir_order
    runtime_iteration: unspecified
```

Do not rely on runtime ordering unless future golden references prove it.

---

## 13. List

## 13.1 Runtime Type

```yaml
List:
  runtime_type: System.Collections.Generic.List[System.String]
  element_type: Text
  ordered: true
  indexed: true
  zero_based: true
  duplicates_allowed: true
```

Confirmed runtime type is `List<String>`.

## 13.2 Supported Logical Operations

```yaml
blocks:
  ListClear:
    operation: clear

  ListAddItem:
    operation: append

  ListInsertItem:
    operation: insert

  ListRemoveItem:
    operation: remove_first_value

  ListRemoveAtItem:
    operation: remove_at
    returns_removed_value: true

  ListRemoveDuplicates:
    operation: distinct_in_place

  ListSort:
    operation: sort_in_place
    directions: [Ascending, Descending]
```

## 13.3 Determinism

For `List`, order is part of data and must be preserved from IR.

```yaml
collection_order:
  List:
    preserve_ir_order: true
    runtime_iteration: deterministic
```

## 13.4 Initial Serialization

Not fully implemented yet as deterministic writer.
Current safe strategy:

- use verified empty template, or
- create and populate at runtime with list blocks

---

## 14. DataTable

## 14.1 Runtime Type

```yaml
DataTable:
  runtime_type: System.Data.DataTable
  mutable: true
  ordered_rows: true
  ordered_columns: true
  row_index_base: 0
```

## 14.2 Confirmed Outer Storage

Confirmed storage model:

- binary container for `System.Data.DataTable`
- includes:
  - `XmlSchema`
  - `XmlDiffGram`

### High-level model

```text
Base64
  └── binary container
      └── System.Data.DataTable
          ├── XmlSchema
          ├── XmlDiffGram
          └── metadata
```

## 14.3 Confirmed Table Samples

Confirmed:
- empty table
- schema-only table
- table with schema and rows

## 14.4 Confirmed Column Types in UI

Observed in editor:

```yaml
datatable_column_types:
  scalar:
    - String
    - Int32
    - Double
    - Decimal
    - DateTime
    - Boolean
    - Byte
    - Char
    - Guid
    - Int16
    - Int64
    - SByte
    - Single
    - TimeSpan
    - UInt16
    - UInt32
    - UInt64

  complex:
    - DataTable
    - Hashtable
```

Presence in UI does not equal confirmed serializer support.

## 14.5 Confirmed Mapping

```yaml
datatable_type_mapping:
  String:
    xsd_type: xs:string

  Int32:
    xsd_type: xs:int

  Double:
    xsd_type: xs:double

  Decimal:
    xsd_type: xs:decimal

  DateTime:
    xsd_type: xs:dateTime

  Boolean:
    xsd_type: xs:boolean

  Guid:
    xsd_type: xs:string
    msdata_type: "System.Guid, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
```

## 14.6 Confirmed Null Policy

`DBNull` is represented by omission of cell element in DiffGram.

```yaml
null_policy:
  value: DBNull
  diffgram_encoding: omit_column_element
```

Distinguish carefully from empty string.

## 14.7 Confirmed Value Rules

### DateTime
Stored as ISO 8601 with offset.

Example:

```text
2026-10-15T17:30:45+04:00
```

### Guid
Stored as canonical textual GUID, lowercase preferred.

### Boolean
Inside `DataTable` cells:

```yaml
Boolean:
  datatable_cell:
    true: "true"
    false: "false"
```

This differs from `Logical` declaration storage.

### String
Stored as normal XML text with XML escaping rules.

### Newline
In DiffGram cell text, newline is actual line break, not PowerShell escaped literal.

## 14.8 DiffGram Rules

```yaml
diffgram:
  dataset_name: tmpDataSet
  table_name: Table1
  row_id:
    format: "Table1{index}"
    start: 1
  row_order:
    start: 0
  has_changes: inserted
  omit_null_cells: true
```

## 14.9 Safe MVP Policy

DataTable deterministic support currently valid for:

- schema + rows with confirmed cell types:
  - String
  - Int32
  - Decimal
  - Double
  - DateTime
  - Guid
  - Boolean
  - DBNull

Not yet safe for nested confirmed generation:
- Hashtable cell values
- DataTable cell values
- untested rare numeric types
- TimeSpan values
- Char values

---

## 15. Assign Block

## 15.1 Identity

```yaml
Assign:
  internal_name: Assign
  version: "166"
  linux_available: true
```

## 15.2 Assignment Model

Supports up to ten assignment pairs:

```text
Variable1 + Expression1
...
Variable10 + Expression10
```

Unused pairs remain in template and should not be deleted from internal content structure.

## 15.3 IR

```yaml
- key: assign_number
  type: Assign
  assignments:
    - target: number_1
      expression:
        kind: number_literal
        value: 10
```

## 15.4 Notes

- `VariableN` usually stores target with `$`.
- `ExpressionN` stores PowerShell-compatible expression.
- Multiple assignments within one block execute sequentially.

---

## 16. Condition Block

## 16.1 Identity

```yaml
Condition:
  display_name: Условие
  internal_name: IfElse
  version: "23"
  linux_available: true
```

## 16.2 Core Meaning

Evaluates a logical expression and branches:

- `Yes` if true
- `No` if false

## 16.3 Connectors

```yaml
connectors:
  inputs:
    - Input
    - InputLeft
  outputs:
    - Yes
    - No
    - Error
```

No normal `Next` output should be generated.

## 16.4 Property

```yaml
properties:
  Condition:
    type: Expression
    value_type: Logical
    required: true
    ps_verified: true
```

## 16.5 IR

```yaml
- key: check_enabled
  type: Condition
  condition:
    kind: variable_reference
    variable: Enabled
  branches:
    yes: enabled_action
    no: disabled_action
    error: error_handler
```

## 16.6 Expression Policy

Supported IR expression categories:

- variable reference
- numeric comparison
- string comparison
- logical composition
- function result if confirmed boolean-producing

In strict mode, avoid implicit truthiness for non-logical values.

---

## 17. While Block

## 17.1 Identity

```yaml
WhileLoop:
  display_name: Цикл Пока
  internal_name: While
  version: "32"
  linux_available: true
```

## 17.2 Core Meaning

Pre-test loop:

- evaluate condition before each iteration
- if true → go to `Iteration`
- if false → go to `Next`

## 17.3 Connectors

```yaml
connectors:
  inputs:
    - Input
    - InputLeft
  outputs:
    - Iteration
    - Next
```

Static `Error` connector is not yet confirmed for generation.

## 17.4 Important Current Limitation

Current golden file confirms block template and graph form, but has empty `Condition/@Value`.

Therefore:
- XML shell and graph semantics are confirmed.
- Filled condition examples still need stronger golden confirmation.
- Generator must not emit empty condition in release mode.

## 17.5 Graph Rule

Body must return from last body block to `While/Input`.

Example:

```text
While.Iteration → first body block
last body block → While.Input
While.Next → next block after loop
```

## 17.6 IR

```yaml
- key: loop_1
  type: WhileLoop
  condition:
    kind: compare
    operator: lt
    left:
      kind: variable_reference
      variable: Counter
    right:
      kind: number_literal
      value: 3
  body:
    entry: increment
    exit: increment
  next: stop_1
```

## 17.7 Safety Heuristics

Validator should warn if:

- body contains only `ToDo`
- body has no state change affecting condition
- body has no delay and no useful work
- condition statically always true

---

## 18. Collection and DataTable Blocks

## 18.1 Hashtable/Dictionary Operations

Confirmed logical operations:

- clear
- set/add item
- remove item
- contains key
- contains value

## 18.2 List Operations

Confirmed logical operations:

- clear
- add
- insert
- remove by value
- remove by index
- remove duplicates
- sort

## 18.3 DataTable Operations

Confirmed logical operations:

- get cell value
- contains column
- select rows
- merge
- insert table
- transpose
- convert to hashtable/dictionary
- convert to list

### Important Type Caveat

Some block property `ValueType` fields represent block-category expectations, not true runtime .NET type. Runtime type inference must not rely only on `Property/@ValueType`.

---

## 19. Expression Model

## 19.1 Categories

```yaml
expression_kinds:
  variable_reference
  number_literal
  boolean_literal
  string_literal
  compare
  logical
  arithmetic
  powershell
  secure_encode
  secure_decode
```

## 19.2 Compiler Rules

- Add `$` to expression variable references.
- Use parentheses aggressively for deterministic and unambiguous expressions.
- Do not store XML-escaped strings in IR.
- Escape only at serialization stage.

## 19.3 Strictness

In strict mode:

- expression result type must be compatible with destination
- condition results must be logical
- no ambiguous coercion unless explicitly confirmed

---

## 20. Validation Split

## 20.1 Structural Validator Responsibilities

Structural validator checks:

- required elements and attributes
- internal block identity
- connector presence
- connector graph shape
- basic uniqueness and shape constraints
- profile-dependent structural rules

Typical examples:

- missing `Condition`
- missing branch connector
- invalid `InternalName`
- missing `Items`
- wrong property shape

## 20.2 Semantic Validator Responsibilities

Semantic validator checks:

- Base64 decoding
- binary container correctness
- PowerShell expression parsing
- variable references
- type compatibility
- deterministic serializer stability
- possible infinite loops
- unsupported runtime type use
- unresolved placeholders in release mode

---

## 21. Schematron / Structural Rule Families

Suggested structural code families:

```yaml
rule_families:
  SHP-DOC: document-level rules
  SHP-ID: identifiers
  SHP-GRF: graph structure
  SHP-BLK: generic block structure
  SHP-PRP: property shape
  SHP-VAR: variable declarations
  SHP-ASG: assign rules
  SHP-TODO: placeholder rules
  SHP-SEC: secure string rules
  SHP-COL: collection rules
  SHP-LIST: list rules
  SHP-DT: datatable block rules
  SHP-DT-SER: datatable serialization rules
  SHP-COND: condition rules
  SHP-WHILE: while rules
  SHP-TEXT: text serialization rules
```

---

## 22. Semantic Rule Themes

Recommended semantic code families:

```yaml
semantic_rule_families:
  SEM-EXPR
  SEM-TYPE
  SEM-SER
  SEM-LOOP
  SEM-DET
  SEM-REF
```

Examples:

- unresolved variable reference
- type mismatch
- unstable serializer output
- loop condition never changes
- unsupported nested type
- declaration/runtime mismatch

---

## 23. Draft vs Release Policy

## 23.1 Draft

Allowed:

- `ToDo`
- partially supported generation where explicitly marked safe
- warnings for unresolved external variables

Purpose:
- fast iteration
- graph preservation
- prototype generation

## 23.2 Release

Forbidden:

- unresolved `ToDo`
- empty `While` condition
- unsupported serializer branches
- ambiguous type coercion not explicitly allowed
- unvalidated nested complex cells

Purpose:
- only confirmed and validated constructs

---

## 24. External Variables

Expressions may refer to variables not declared in diagram properties.

Support model:

```yaml
external_variables:
  - name: MySecureString
    expected_type: SecureString
```

Unknown reference policy:

- warn by default
- suppress warning if declared external
- do not silently auto-create unless generation mode explicitly requests it

---

## 25. Determinism Rules

Determinism must cover:

- UUID generation
- node order
- edge order
- connector serialization order
- attribute ordering
- declaration serialization
- DataTable schema ordering
- DiffGram row ordering
- newline convention
- formatting convention for numeric values
- normalized GUID output

Suggested fixed connector order for branching blocks:

```yaml
connector_order:
  Condition: [Yes, No, Error]
  While: [Iteration, Next]
```

---

## 26. Confirmed / Partially Confirmed Support Matrix

```yaml
support_matrix:
  ToDo:
    xml_template: confirmed
    runtime_behavior: confirmed

  Assign:
    xml_template: confirmed
    runtime_behavior: confirmed

  Condition:
    xml_template: confirmed
    logical_variable_condition: confirmed
    complex_expressions: syntax-supported
    full_golden_complex_expression: partial

  While:
    xml_template: confirmed
    graph_semantics: confirmed
    filled_condition_golden: partial

  Text:
    declaration_serializer: confirmed
    runtime_assignment: confirmed

  SecureString:
    declaration_empty: confirmed
    tosecure: confirmed
    fromsecure: confirmed

  Hashtable:
    runtime_type: confirmed
    blocks: confirmed

  Dictionary:
    runtime_type: confirmed
    blocks: confirmed
    ordered_iteration: not_confirmed

  List:
    runtime_type: confirmed
    block_operations: confirmed
    initial_serializer: partial

  DataTable:
    runtime_type: confirmed
    outer_container: confirmed
    schema_only: confirmed
    rows: confirmed
    supported_cell_types:
      - String
      - Int32
      - Double
      - Decimal
      - DateTime
      - Boolean
      - Guid
      - DBNull
    nested_complex_cells: not_confirmed
```

---

## 27. Current Safe MVP

The currently realistic MVP for agent testing:

### Can generate
- Process graph with:
  - Start
  - Stop
  - ToDo
  - Assign
  - Condition
  - While
- Variables of type:
  - Number
  - Logical
  - Text
  - SecureString
- Collection variables:
  - Hashtable
  - Dictionary
  - List
- DataTable declarations for confirmed subset
- Confirmed collection/list/table operation blocks
- Draft and release validation profile behavior

### Should avoid in MVP
- Non-confirmed nested serializers
- Broad PowerShell free-form generation without typing
- Runtime order assumptions for dictionary
- Filled `While` conditions treated as fully golden-confirmed
- Complex table cell object graphs

---

## 28. Recommended File Outputs for Real Package

When this master context is later split into actual skill package files, recommended mapping is:

```text
master-context.md           -> canonical single-file context
SKILL.md                    -> agent-facing operating instructions
README.md                   -> human usage guide
config/block-catalog.yaml   -> block facts
config/type-catalog.yaml    -> type facts
config/validation-profiles.yaml -> draft/release/strict behavior
schemas/sherpa-rules.sch    -> structural Schematron
references/*.md             -> explanatory docs
```

---

## 29. Open Questions

Remaining important unknowns:

1. Filled `While` condition golden sample with non-empty expression.
2. Full serializer for initial `List<String>`.
3. Nested `Hashtable` in `DataTable` cell.
4. Nested `DataTable` in `DataTable` cell.
5. Golden samples for:
   - `TimeSpan`
   - `Char`
   - `Single`
   - `Byte`
   - `SByte`
   - `Int16`
   - `Int64`
   - unsigned integer family
6. More confirmed expression patterns in `IfElse` and `While`.
7. Whether any additional dynamic error connectors should be serialized explicitly for loop blocks.
8. Full deterministic binary writer implementation for DataTable container.
9. Whether additional block versions vary across Sherpa versions and need versioned templates.
10. Canonical layout strategy compatible with Sherpa Designer rendering expectations.

---

## 30. Recommended Next Golden Samples

Priority order:

### Priority 1
Filled `While` loop sample:

- `Counter = 0`
- condition: `$Counter -lt 3`
- body increments counter
- returns to `While/Input`
- `Next` goes to `Stop`

### Priority 2
List declaration serializer sample:

- empty list
- list with 2–3 string values
- compare declaration Base64

### Priority 3
Nested DataTable cell types:

- column type `Hashtable`
- column type `DataTable`
- one non-empty value each

### Priority 4
Condition sample with compound expression:

```powershell
($Number -gt 5) -and ($Text -eq "Test")
```

### Priority 5
Rare DataTable primitive column types:
- `TimeSpan`
- `Char`
- `Single`
- `Byte`
- `Int16`
- `Int64`
- unsigned family

---

## 31. Agent Usage Notes

When an agent uses this file:

1. Treat this document as canonical project memory.
2. Prefer confirmed rules over inferred behavior.
3. Distinguish:
   - confirmed
   - partial
   - open
4. Never upgrade a hypothesis to confirmed without a new golden reference.
5. When generating package files, derive machine-readable catalogs from this file, not from raw conversation history.

---

## 32. Compact Canonical Catalog

### 32.1 Confirmed Runtime Types

```yaml
confirmed_runtime_types:
  Hashtable: System.Collections.Hashtable
  Dictionary: System.Collections.Generic.Dictionary[System.Object,System.Object]
  List: System.Collections.Generic.List[System.String]
  DataTable: System.Data.DataTable
```

### 32.2 Confirmed Core Blocks

```yaml
confirmed_blocks:
  ToDo:
    internal_name: ToDoBlock

  Assign:
    internal_name: Assign
    version: "166"

  Condition:
    internal_name: IfElse
    version: "23"

  While:
    internal_name: While
    version: "32"
```

### 32.3 Confirmed Variable Types

```yaml
confirmed_variable_types:
  - Number
  - Logical
  - Text
  - SecureString
  - Hashtable
  - Dictionary
  - List
  - DataTable
```

---

## 33. Change Log

### v0.3-draft
- Consolidated all currently confirmed findings into single master context.
- Recorded correction that Sherpa `Dictionary` runtime type is `Dictionary<Object,Object>`, not ordered dictionary.
- Recorded `Text` initial value serializer model.
- Recorded `DataTable` confirmed storage model with XSD + DiffGram.
- Added block-level models for `Assign`, `Condition`, `While`.
- Added draft/release/strict policy model.
- Added safe MVP boundary.

---