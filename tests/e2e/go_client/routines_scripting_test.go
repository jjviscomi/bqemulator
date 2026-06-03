// Phase 6 routines + scripting E2E for the bqemulator via the Google
// Go BigQuery client. Matches the Phase 6 ship criterion.

package e2e

import (
	"context"
	"fmt"
	"testing"
	"time"

	"cloud.google.com/go/bigquery"
	"google.golang.org/api/iterator"
	"google.golang.org/api/option"
)

func routines_scriptingClient(ctx context.Context, t *testing.T) *bigquery.Client {
	t.Helper()
	client, err := bigquery.NewClient(
		ctx,
		"e2e-go-routines_scripting",
		option.WithEndpoint(bqAPIBase()),
		option.WithoutAuthentication(),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func createRoutine(ctx context.Context, t *testing.T, client *bigquery.Client, datasetID, routineID string, meta *bigquery.RoutineMetadata) {
	t.Helper()
	r := client.Dataset(datasetID).Routine(routineID)
	if err := r.Create(ctx, meta); err != nil {
		t.Fatalf("create routine %s: %v", routineID, err)
	}
}

func TestRoutinesScriptingShipCriterion(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	client := routines_scriptingClient(ctx, t)
	defer client.Close()

	dsID := "routines_scripting_go_ds"
	ds := client.Dataset(dsID)
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() {
		if err := ds.DeleteWithContents(ctx); err != nil {
			t.Logf("cleanup: %v", err)
		}
	}()

	createRoutine(ctx, t, client, dsID, "sql_inc", &bigquery.RoutineMetadata{
		Type:     "SCALAR_FUNCTION",
		Language: "SQL",
		Arguments: []*bigquery.RoutineArgument{
			{Name: "x", DataType: &bigquery.StandardSQLDataType{TypeKind: "INT64"}},
		},
		ReturnType: &bigquery.StandardSQLDataType{TypeKind: "INT64"},
		Body:       "x + 1",
	})

	createRoutine(ctx, t, client, dsID, "js_double", &bigquery.RoutineMetadata{
		Type:     "SCALAR_FUNCTION",
		Language: "JAVASCRIPT",
		Arguments: []*bigquery.RoutineArgument{
			{Name: "x", DataType: &bigquery.StandardSQLDataType{TypeKind: "INT64"}},
		},
		ReturnType: &bigquery.StandardSQLDataType{TypeKind: "INT64"},
		Body:       "return x * 2;",
	})

	createRoutine(ctx, t, client, dsID, "one_to_n", &bigquery.RoutineMetadata{
		Type:     "TABLE_VALUED_FUNCTION",
		Language: "SQL",
		Arguments: []*bigquery.RoutineArgument{
			{Name: "n", DataType: &bigquery.StandardSQLDataType{TypeKind: "INT64"}},
		},
		Body: "SELECT i AS value FROM UNNEST(GENERATE_ARRAY(1, n)) AS i",
	})

	script := fmt.Sprintf(`
DECLARE n INT64 DEFAULT 3;
DECLARE total INT64 DEFAULT 0;
BEGIN
  FOR row IN (SELECT value FROM %s.one_to_n(n)) DO
    SET total = total + %s.js_double(%s.sql_inc(row.value));
  END FOR;
EXCEPTION WHEN ERROR THEN
  SET total = -1;
END;
IF total > 0 THEN
  SELECT total AS answer;
ELSE
  SELECT -1 AS answer;
END IF;
`, dsID, dsID, dsID)

	q := client.Query(script)
	it, err := q.Read(ctx)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	var row []bigquery.Value
	if err := it.Next(&row); err != nil && err != iterator.Done {
		t.Fatalf("row: %v", err)
	}
	if v, ok := row[0].(int64); !ok || v != 18 {
		t.Fatalf("expected answer=18, got %v", row[0])
	}
}

// TestScriptedCreateSchemaIsListed checks that a CREATE SCHEMA inside a
// multi-statement script registers the dataset in the catalog so it
// surfaces via datasets.list and datasets.get. A single-statement
// CREATE SCHEMA takes the executor fast path; the trailing SELECT tips
// this job into the scripting interpreter, whose DDL-sync hook must run.
func TestScriptedCreateSchemaIsListed(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	client := routines_scriptingClient(ctx, t)
	defer client.Close()

	dsID := "scripted_created_schema_go_ds"
	ds := client.Dataset(dsID)
	// Guard against a stale dataset left by an interrupted run.
	_ = ds.DeleteWithContents(ctx)
	defer func() {
		if err := ds.DeleteWithContents(ctx); err != nil {
			t.Logf("cleanup: %v", err)
		}
	}()

	script := fmt.Sprintf("CREATE SCHEMA `%s`;\nSELECT 1 AS n;", dsID)
	if _, err := client.Query(script).Read(ctx); err != nil {
		t.Fatalf("script query: %v", err)
	}

	found := false
	it := client.Datasets(ctx)
	for {
		d, err := it.Next()
		if err == iterator.Done {
			break
		}
		if err != nil {
			t.Fatalf("list datasets: %v", err)
		}
		if d.DatasetID == dsID {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("dataset %q absent from datasets.list after scripted CREATE SCHEMA", dsID)
	}

	if _, err := ds.Metadata(ctx); err != nil {
		t.Fatalf("datasets.get %q: %v", dsID, err)
	}
}

// TestScriptEndingInDDLReturnsEmpty verifies last-statement-wins: a
// multi-statement script ending in DDL returns an empty result, not the
// prior SELECT's rows (BigQuery returns the final statement's result set).
func TestScriptEndingInDDLReturnsEmpty(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	client := routines_scriptingClient(ctx, t)
	defer client.Close()

	dsID := "script_result_ddl_last_go"
	ds := client.Dataset(dsID)
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() {
		if err := ds.DeleteWithContents(ctx); err != nil {
			t.Logf("cleanup: %v", err)
		}
	}()

	script := fmt.Sprintf("SELECT 1 AS a;\nCREATE TABLE `%s.%s.trailing` (id INT64)", client.Project(), dsID)
	it, err := client.Query(script).Read(ctx)
	if err != nil {
		t.Fatalf("script query: %v", err)
	}
	var row []bigquery.Value
	if err := it.Next(&row); err != iterator.Done {
		t.Fatalf("expected empty result (iterator.Done), got row=%v err=%v", row, err)
	}
}

// TestRoutinesScriptingSingleRoutineDDLStatementType verifies that a
// single routine DDL statement reports BigQuery's statementType, and that
// DROP routine statements execute against the live container. CREATE
// PROCEDURE reports SCRIPT — BigQuery classifies a procedure definition
// as a script. Pinned by the routines_scripting/routine_ddl_*
// conformance corpus.
func TestRoutinesScriptingSingleRoutineDDLStatementType(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	client := routines_scriptingClient(ctx, t)
	defer client.Close()

	dsID := "routine_ddl_go"
	ds := client.Dataset(dsID)
	_ = ds.DeleteWithContents(ctx)
	if err := ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	defer func() {
		if err := ds.DeleteWithContents(ctx); err != nil {
			t.Logf("cleanup: %v", err)
		}
	}()

	runDDL := func(sql string) string {
		job, err := client.Query(sql).Run(ctx)
		if err != nil {
			t.Fatalf("run %q: %v", sql, err)
		}
		status, err := job.Wait(ctx)
		if err != nil {
			t.Fatalf("wait %q: %v", sql, err)
		}
		if err := status.Err(); err != nil {
			t.Fatalf("job %q failed: %v", sql, err)
		}
		qs, ok := status.Statistics.Details.(*bigquery.QueryStatistics)
		if !ok {
			t.Fatalf("no QueryStatistics for %q", sql)
		}
		return qs.StatementType
	}

	proj := client.Project()
	cases := []struct{ sql, want string }{
		{fmt.Sprintf("CREATE FUNCTION `%s.%s.add_one`(x INT64) RETURNS INT64 AS (x + 1)", proj, dsID), "CREATE_FUNCTION"},
		{fmt.Sprintf("CREATE TABLE FUNCTION `%s.%s.one_row`(n INT64) AS SELECT n AS x", proj, dsID), "CREATE_TABLE_FUNCTION"},
		{fmt.Sprintf("CREATE PROCEDURE `%s.%s.noop`() BEGIN SELECT 1 AS one; END", proj, dsID), "SCRIPT"},
		{fmt.Sprintf("DROP FUNCTION `%s.%s.add_one`", proj, dsID), "DROP_FUNCTION"},
		{fmt.Sprintf("DROP PROCEDURE `%s.%s.noop`", proj, dsID), "DROP_PROCEDURE"},
		{fmt.Sprintf("DROP TABLE FUNCTION `%s.%s.one_row`", proj, dsID), "DROP_TABLE_FUNCTION"},
	}
	for _, c := range cases {
		if got := runDDL(c.sql); got != c.want {
			t.Fatalf("statementType for %q = %q, want %q", c.sql, got, c.want)
		}
	}
}
