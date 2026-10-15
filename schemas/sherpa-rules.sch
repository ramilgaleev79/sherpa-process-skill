<?xml version="1.0" encoding="UTF-8"?>
<sch:schema
    xmlns:sch="http://purl.oclc.org/dsdl/schematron"
    xmlns:xs="http://www.w3.org/2001/XMLSchema"
    queryBinding="xslt2">

  <sch:title>Sherpa RPA deterministic XML validation rules</sch:title>

  <sch:ns prefix="x" uri="urn:sherpa:rpa:generic"/>

  <!--
    CANONICAL PHASES
    structural    : базовая структура XML, обязательные атрибуты, коннекторы, шаблоны блоков
    semantic-lite : только те проверки, которые можно сделать без декодирования и без PowerShell AST
    strict        : structural + semantic-lite, для release-профиля
  -->

  <sch:phase id="structural">
    <sch:active pattern="document-rules"/>
    <sch:active pattern="diagram-property-rules"/>
    <sch:active pattern="assign-rules"/>
    <sch:active pattern="todo-rules"/>
    <sch:active pattern="collection-rules"/>
    <sch:active pattern="list-rules"/>
    <sch:active pattern="datatable-rules"/>
    <sch:active pattern="condition-rules"/>
    <sch:active pattern="while-rules"/>
  </sch:phase>

  <sch:phase id="semantic-lite">
    <sch:active pattern="document-rules"/>
    <sch:active pattern="diagram-property-rules"/>
    <sch:active pattern="assign-rules"/>
    <sch:active pattern="todo-rules"/>
    <sch:active pattern="collection-rules"/>
    <sch:active pattern="list-rules"/>
    <sch:active pattern="datatable-rules"/>
    <sch:active pattern="condition-rules"/>
    <sch:active pattern="while-rules"/>
  </sch:phase>

  <sch:phase id="strict">
    <sch:active pattern="document-rules"/>
    <sch:active pattern="diagram-property-rules"/>
    <sch:active pattern="assign-rules"/>
    <sch:active pattern="todo-rules"/>
    <sch:active pattern="collection-rules"/>
    <sch:active pattern="list-rules"/>
    <sch:active pattern="datatable-rules"/>
    <sch:active pattern="condition-rules"/>
    <sch:active pattern="while-rules"/>
  </sch:phase>

  <!-- ========================================================= -->
  <!-- DOCUMENT RULES                                             -->
  <!-- ========================================================= -->

  <sch:pattern id="document-rules">
    <sch:rule context="/*">
      <sch:assert test="name()='Root' or local-name()='Root'">
        SHP-DOC-001 ERROR: корневой элемент должен быть Root.
      </sch:assert>
      <sch:assert test="count(.//Properties) &gt;= 1">
        SHP-DOC-002 ERROR: в процессе должен присутствовать раздел Properties.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='DiagramProperty']">
      <sch:assert test="string-length(normalize-space(@Name)) &gt; 0">
        SHP-DOC-003 ERROR: DiagramProperty/@Name не должен быть пустым.
      </sch:assert>
    </sch:rule>
  </sch:pattern>

  <!-- ========================================================= -->
  <!-- DIAGRAM PROPERTY RULES                                     -->
  <!-- ========================================================= -->

  <sch:pattern id="diagram-property-rules">

    <sch:rule context="//*[local-name()='DiagramProperty']">
      <sch:assert test="string-length(@Type) &gt; 0">
        SHP-VAR-003 ERROR: DiagramProperty/@Type обязателен.
      </sch:assert>
      <sch:assert test="@IsGlobal">
        SHP-VAR-004 ERROR: DiagramProperty/@IsGlobal обязателен.
      </sch:assert>
      <sch:assert test="@DefaultValue">
        SHP-VAR-004 ERROR: DiagramProperty/@DefaultValue обязателен.
      </sch:assert>
      <sch:assert test="@Value">
        SHP-VAR-004 ERROR: DiagramProperty/@Value обязателен.
      </sch:assert>
      <sch:assert test="@IsGlobal='True' or @IsGlobal='False'">
        SHP-VAR-005 ERROR: IsGlobal должен быть True или False.
      </sch:assert>
      <sch:assert test="not(starts-with(@Name, '$'))">
        SHP-VAR-007 ERROR: имя переменной в декларации не должно начинаться с $.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='Properties']">
      <sch:assert test="count(*[local-name()='DiagramProperty']) = count(distinct-values(*[local-name()='DiagramProperty']/@Name))">
        SHP-VAR-002 ERROR: имена объявленных переменных должны быть уникальными.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='DiagramProperty'][@Type='Logical']">
      <sch:assert test="(@DefaultValue='True' or @DefaultValue='False') and (@Value='True' or @Value='False')">
        SHP-VAR-006 ERROR: Logical должен хранить только True или False в DefaultValue и Value.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='DiagramProperty'][@Type='Text']">
      <sch:assert test="string-length(@DefaultValue) &gt; 0 and string-length(@Value) &gt; 0">
        SHP-TEXT-001 ERROR: Text должен иметь непустые DefaultValue и Value.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='DiagramProperty'][@Type='Text' or @Type='DataTable']">
      <sch:assert test="@DefaultValue = @Value">
        SHP-TEXT-008 WARN: DefaultValue и Value различаются для неизменённого начального состояния.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='DiagramProperty'][@Type='SecureString']">
      <sch:assert test="@DefaultValue = '' and @Value = ''">
        SHP-SEC-009 ERROR: SecureString не должен сохраняться как открытый текст в XML.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='DiagramProperty'][@Type='DataTable']">
      <sch:assert test="string-length(@DefaultValue) &gt; 0">
        SHP-DT-SER-001 ERROR: DataTable/@DefaultValue отсутствует.
      </sch:assert>
      <sch:assert test="string-length(@Value) &gt; 0">
        SHP-DT-SER-001 ERROR: DataTable/@Value отсутствует.
      </sch:assert>
      <sch:assert test="@DefaultValue = @Value">
        SHP-DT-SER-012 WARN: DataTable DefaultValue и Value различаются.
      </sch:assert>
    </sch:rule>

  </sch:pattern>

  <!-- ========================================================= -->
  <!-- ASSIGN RULES                                               -->
  <!-- ========================================================= -->

  <sch:pattern id="assign-rules">

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='Assign']">
      <sch:assert test="@Version='166'">
        SHP-ASG-000 ERROR: подтверждённая версия блока Assign должна быть 166.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='Assign']//*[local-name()='Property'][starts-with(@Name,'Variable')]">
      <sch:let name="suffix" value="substring-after(@Name,'Variable')"/>
      <sch:assert test="@Type='Variable'">
        SHP-ASG-003 ERROR: VariableN/@Type должен быть Variable.
      </sch:assert>
      <sch:assert test="@ValueType='Variable'">
        SHP-ASG-004 ERROR: VariableN/@ValueType должен быть Variable.
      </sch:assert>
      <sch:assert test="string-length(@Value)=0 or starts-with(@Value,'$')">
        SHP-ASG-007 ERROR: VariableN/@Value должен быть пустым либо начинаться с $.
      </sch:assert>
      <sch:assert test="count(../*[local-name()='Property'][@Name=concat('Expression',$suffix)])=1">
        SHP-EXPR-STR-005 ERROR: для VariableN должен существовать соответствующий ExpressionN.
      </sch:assert>
      <sch:assert test="not(string-length(@Value)&gt;0) or string-length(../*[local-name()='Property'][@Name=concat('Expression',$suffix)]/@Value)&gt;0">
        SHP-ASG-001 ERROR: заполненный VariableN требует заполненный ExpressionN.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='Assign']//*[local-name()='Property'][starts-with(@Name,'Expression')]">
      <sch:let name="suffix" value="substring-after(@Name,'Expression')"/>
      <sch:assert test="@Type='Expression'">
        SHP-ASG-005 ERROR: ExpressionN/@Type должен быть Expression.
      </sch:assert>
      <sch:assert test="@ValueType='AnyDataType'">
        SHP-ASG-006 ERROR: ExpressionN/@ValueType должен быть AnyDataType.
      </sch:assert>
      <sch:assert test="count(../*[local-name()='Property'][@Name=concat('Variable',$suffix)])=1">
        SHP-EXPR-STR-005 ERROR: для ExpressionN должен существовать соответствующий VariableN.
      </sch:assert>
      <sch:assert test="not(string-length(@Value)&gt;0) or string-length(../*[local-name()='Property'][@Name=concat('Variable',$suffix)]/@Value)&gt;0">
        SHP-ASG-002 ERROR: заполненный ExpressionN требует заполненный VariableN.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='Assign']">
      <sch:assert test="count(.//*[local-name()='Property'][starts-with(@Name,'Variable')]) &lt;= 10">
        SHP-ASG-008 ERROR: в одном Assign разрешено не более 10 VariableN.
      </sch:assert>
      <sch:assert test="count(.//*[local-name()='Property'][starts-with(@Name,'Expression')]) &lt;= 10">
        SHP-ASG-008 ERROR: в одном Assign разрешено не более 10 ExpressionN.
      </sch:assert>
    </sch:rule>

  </sch:pattern>

  <!-- ========================================================= -->
  <!-- TODO RULES                                                 -->
  <!-- ========================================================= -->

  <sch:pattern id="todo-rules">

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='ToDoBlock']">
      <sch:assert test="@DisplayName='ToDo'">
        SHP-TODO-001 WARN: ToDoBlock ожидается с DisplayName='ToDo'.
      </sch:assert>
      <sch:assert test="count(.//*[local-name()='Property']) = 0">
        SHP-TODO-005 ERROR: у ToDoBlock не должно быть подтверждённых свойств.
      </sch:assert>
    </sch:rule>

  </sch:pattern>

  <!-- ========================================================= -->
  <!-- COLLECTION RULES (Hashtable / Dictionary)                  -->
  <!-- ========================================================= -->

  <sch:pattern id="collection-rules">

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='HashtableClear' or @InternalName='HashtableAddItem' or @InternalName='HashtableRemoveItem' or @InternalName='HashtableContainsKey' or @InternalName='HashtableContainsValue']//*[local-name()='Property'][@Name='Items']">
      <sch:assert test="@Type='Variable'">
        SHP-COL-002 ERROR: Items/@Type должен быть Variable.
      </sch:assert>
      <sch:assert test="@ValueType='Hashtable'">
        SHP-COL-003 ERROR: Items/@ValueType должен быть Hashtable.
      </sch:assert>
      <sch:assert test="not(starts-with(@Value,'$'))">
        SHP-COL-005 ERROR: Items/@Value не должен содержать префикс $.
      </sch:assert>
      <sch:assert test="string-length(normalize-space(@Value)) &gt; 0">
        SHP-COL-001 ERROR: Items должен быть заполнен.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='HashtableAddItem']">
      <sch:assert test="count(.//*[local-name()='Property'][@Name='Key' and string-length(@Value)&gt;0]) = 1">
        SHP-COL-007 ERROR: AddItem требует непустой Key.
      </sch:assert>
      <sch:assert test="count(.//*[local-name()='Property'][@Name='Value' and string-length(@Value)&gt;0]) = 1">
        SHP-COL-007 ERROR: AddItem требует непустой Value.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='HashtableRemoveItem']">
      <sch:assert test="count(.//*[local-name()='Property'][@Name='Key' and string-length(@Value)&gt;0]) = 1">
        SHP-COL-008 ERROR: RemoveItem требует непустой Key.
      </sch:assert>
    </sch:rule>

  </sch:pattern>

  <!-- ========================================================= -->
  <!-- LIST RULES                                                 -->
  <!-- ========================================================= -->

  <sch:pattern id="list-rules">

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][starts-with(@InternalName,'List')]//*[local-name()='Property'][@Name='Items']">
      <sch:assert test="@Type='Variable'">
        SHP-LIST-002 ERROR: Items/@Type должен быть Variable.
      </sch:assert>
      <sch:assert test="@ValueType='List'">
        SHP-LIST-003 ERROR: Items/@ValueType должен быть List.
      </sch:assert>
      <sch:assert test="not(starts-with(@Value,'$'))">
        SHP-LIST-004 ERROR: Items/@Value не должен начинаться с $.
      </sch:assert>
      <sch:assert test="string-length(normalize-space(@Value)) &gt; 0">
        SHP-LIST-001 ERROR: Items должен быть заполнен.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='ListAddItem']">
      <sch:assert test="count(.//*[local-name()='Property'][@Name='Value' and string-length(@Value)&gt;0]) = 1">
        SHP-LIST-006 ERROR: ListAddItem требует Value.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='ListInsertItem']">
      <sch:assert test="count(.//*[local-name()='Property'][@Name='Index' and string-length(@Value)&gt;0]) = 1">
        SHP-LIST-007 ERROR: ListInsertItem требует Index.
      </sch:assert>
      <sch:assert test="count(.//*[local-name()='Property'][@Name='Value' and string-length(@Value)&gt;0]) = 1">
        SHP-LIST-007 ERROR: ListInsertItem требует Value.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='ListRemoveItem']">
      <sch:assert test="count(.//*[local-name()='Property'][@Name='Value' and string-length(@Value)&gt;0]) = 1">
        SHP-LIST-011 ERROR: ListRemoveItem требует Value.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='ListRemoveAtItem']">
      <sch:assert test="count(.//*[local-name()='Property'][@Name='Index' and string-length(@Value)&gt;0]) = 1">
        SHP-LIST-012 ERROR: ListRemoveAtItem требует Index.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='ListSort']//*[local-name()='Property'][@Name='Direction']">
      <sch:assert test="@Value='Ascending' or @Value='Descending'">
        SHP-LIST-014 ERROR: ListSort/Direction должен быть Ascending или Descending.
      </sch:assert>
    </sch:rule>

  </sch:pattern>

  <!-- ========================================================= -->
  <!-- DATATABLE RULES                                            -->
  <!-- ========================================================= -->

  <sch:pattern id="datatable-rules">

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='GetCellValue' or @InternalName='ContainsColumn' or @InternalName='SelectRows' or @InternalName='MergeDataTable' or @InternalName='InsertDataTable' or @InternalName='TransposeDataTable' or @InternalName='DataTableToHashtable' or @InternalName='DataTableToList']//*[local-name()='Property'][@Name='Table']">
      <sch:assert test="@Type='Variable'">
        SHP-DT-002 ERROR: Table/@Type должен быть Variable.
      </sch:assert>
      <sch:assert test="@ValueType='DataTable'">
        SHP-DT-003 ERROR: Table/@ValueType должен быть DataTable.
      </sch:assert>
      <sch:assert test="not(starts-with(@Value,'$'))">
        SHP-DT-004 ERROR: Table/@Value не должен содержать $.
      </sch:assert>
      <sch:assert test="string-length(normalize-space(@Value)) &gt; 0">
        SHP-DT-001 ERROR: свойство Table обязательно и должно быть заполнено.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='MergeDataTable']//*[local-name()='Property'][@Name='MissingSchemaAction']">
      <sch:assert test="@Value='Add' or @Value='AddWithKey' or @Value='Error' or @Value='Ignore' or @Value='AddRight'">
        SHP-DT-009 ERROR: MissingSchemaAction должен входить в подтверждённый набор.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='MergeDataTable']">
      <sch:assert test="not(.//*[local-name()='Property'][@Name='MissingSchemaAction']/@Value='AddWithKey') or string-length(normalize-space(.//*[local-name()='Property'][@Name='ColumnForKey']/@Value)) &gt; 0">
        SHP-DT-010 ERROR: для AddWithKey должен быть заполнен ColumnForKey.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='InsertDataTable']">
      <sch:assert test="count(.//*[local-name()='Property'][@Name='Table' and string-length(@Value)&gt;0]) = 1 and count(.//*[local-name()='Property'][@Name='InsertTable' and string-length(@Value)&gt;0]) = 1">
        SHP-DT-012 ERROR: InsertDataTable требует заполненные Table и InsertTable.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='ContainsColumn']">
      <sch:assert test="count(.//*[local-name()='Connector'][@Name='Yes']) &gt;= 1 and count(.//*[local-name()='Connector'][@Name='No']) &gt;= 1">
        SHP-DT-015 ERROR: ContainsColumn должен иметь выходы Yes и No.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='SelectRows']//*[local-name()='Property'][@Name='AsDataTable']">
      <sch:assert test="@Value='True' or @Value='False'">
        SHP-DT-017 ERROR: SelectRows/AsDataTable должен быть True или False.
      </sch:assert>
    </sch:rule>

  </sch:pattern>

  <!-- ========================================================= -->
  <!-- CONDITION RULES                                            -->
  <!-- ========================================================= -->

  <sch:pattern id="condition-rules">

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='IfElse']">
      <sch:assert test="@Version='23'">
        SHP-COND-002 ERROR: подтверждённая версия IfElse должна быть 23.
      </sch:assert>
      <sch:assert test="count(.//*[local-name()='Property'][@Name='Condition']) = 1">
        SHP-COND-003 ERROR: блок Condition должен содержать свойство Condition.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='IfElse']//*[local-name()='Property'][@Name='Condition']">
      <sch:assert test="@Type='Expression'">
        SHP-COND-004 ERROR: Condition/@Type должен быть Expression.
      </sch:assert>
      <sch:assert test="@ValueType='Logical'">
        SHP-COND-005 ERROR: Condition/@ValueType должен быть Logical.
      </sch:assert>
      <sch:assert test="string-length(normalize-space(@Value)) &gt; 0">
        SHP-COND-006 ERROR: Condition/@Value не должен быть пустым.
      </sch:assert>
      <sch:assert test="@IsPSVerified='True'">
        SHP-COND-017 ERROR: Condition должен быть помечен IsPSVerified=True.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='IfElse']">
      <sch:assert test="count(.//*[local-name()='Connector'][@Name='Yes']) &gt;= 1">
        SHP-COND-007 ERROR: блок IfElse должен иметь выход Yes.
      </sch:assert>
      <sch:assert test="count(.//*[local-name()='Connector'][@Name='No']) &gt;= 1">
        SHP-COND-007 ERROR: блок IfElse должен иметь выход No.
      </sch:assert>
      <sch:assert test="count(.//*[local-name()='Connector'][@Name='Next']) = 0">
        SHP-COND-010 ERROR: у блока IfElse не должно быть обычного выхода Next.
      </sch:assert>
    </sch:rule>

  </sch:pattern>

  <!-- ========================================================= -->
  <!-- WHILE RULES                                                -->
  <!-- ========================================================= -->

  <sch:pattern id="while-rules">

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='While']">
      <sch:assert test="@Version='32'">
        SHP-WHILE-002 ERROR: подтверждённая версия блока While должна быть 32.
      </sch:assert>
      <sch:assert test="count(.//*[local-name()='Property'][@Name='Condition']) = 1">
        SHP-WHILE-003 ERROR: While должен содержать свойство Condition.
      </sch:assert>
      <sch:assert test="count(.//*[local-name()='Connector'][@Name='Iteration']) &gt;= 1">
        SHP-WHILE-008 ERROR: While должен иметь выход Iteration.
      </sch:assert>
      <sch:assert test="count(.//*[local-name()='Connector'][@Name='Next']) &gt;= 1">
        SHP-WHILE-009 ERROR: While должен иметь выход Next.
      </sch:assert>
    </sch:rule>

    <sch:rule context="//*[local-name()='ToolboxItemSettings'][@InternalName='While']//*[local-name()='Property'][@Name='Condition']">
      <sch:assert test="@Type='Expression'">
        SHP-WHILE-004 ERROR: While/Condition должен иметь Type=Expression.
      </sch:assert>
      <sch:assert test="@ValueType='AnyDataType'">
        SHP-WHILE-005 ERROR: While/Condition должен иметь ValueType=AnyDataType.
      </sch:assert>
      <sch:assert test="@IsPSVerified='True'">
        SHP-WHILE-006 ERROR: While/Condition должен иметь IsPSVerified=True.
      </sch:assert>
    </sch:rule>

  </sch:pattern>

</sch:schema>