// Package dataflowlocal contains pure transform helpers + a BigQuery
// sink. The transform half is unit-testable without any Docker; the
// sink half is exercised end-to-end against bqemulator.
package dataflowlocal

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"

	"cloud.google.com/go/bigquery"
	"google.golang.org/api/option"
)

// RawEvent is the input row shape (JSON on stdin).
type RawEvent struct {
	ID    int64  `json:"id"`
	Name  string `json:"name"`
	Email string `json:"email"`
}

// CleanEvent is the output row shape (BigQuery target).
type CleanEvent struct {
	ID         int64  `bigquery:"id"`
	Name       string `bigquery:"name"`
	EmailLower string `bigquery:"email_lower"`
}

// Transform lowercases the email and applies a NotPresent default.
func Transform(in RawEvent) CleanEvent {
	email := strings.ToLower(strings.TrimSpace(in.Email))
	if email == "" {
		email = "unknown@example.test"
	}
	return CleanEvent{ID: in.ID, Name: in.Name, EmailLower: email}
}

// ReadEvents decodes NDJSON from r.
func ReadEvents(r io.Reader) ([]RawEvent, error) {
	dec := json.NewDecoder(r)
	var out []RawEvent
	for {
		var ev RawEvent
		if err := dec.Decode(&ev); err != nil {
			if err == io.EOF {
				return out, nil
			}
			return nil, fmt.Errorf("decode: %w", err)
		}
		out = append(out, ev)
	}
}

// Sink writes cleaned events to BigQuery, creating the dataset and
// table on first use.
func Sink(
	ctx context.Context,
	restURL, project, dataset string,
	events []CleanEvent,
) error {
	client, err := bigquery.NewClient(
		ctx,
		project,
		option.WithEndpoint(restURL),
		option.WithoutAuthentication(),
	)
	if err != nil {
		return fmt.Errorf("bigquery.NewClient: %w", err)
	}
	defer func() { _ = client.Close() }()

	ds := client.Dataset(dataset)
	_ = ds.Create(ctx, &bigquery.DatasetMetadata{Location: "US"})

	table := ds.Table("clean_events")
	schema := bigquery.Schema{
		{Name: "id", Type: bigquery.IntegerFieldType},
		{Name: "name", Type: bigquery.StringFieldType},
		{Name: "email_lower", Type: bigquery.StringFieldType},
	}
	_ = table.Create(ctx, &bigquery.TableMetadata{Schema: schema})

	return table.Inserter().Put(ctx, events)
}
