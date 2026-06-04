// SPDX-License-Identifier: Apache-2.0
//
// This file deliberately lives in ``org.apache.beam.sdk.io.gcp.bigquery``
// — Beam's package — so it can reach the ``@VisibleForTesting``
// constructors on ``BigQueryServicesImpl.JobServiceImpl`` and
// ``BigQueryServicesImpl.DatasetServiceImpl`` that accept a pre-built
// ``Bigquery`` client. Those constructors are package-private; same-
// package access works across JARs (the package, not the JAR, defines
// access scope).
//
// Why this exists at all: Beam 2.55.1's Java SDK has no
// ``--bigQueryEndpoint`` option (Beam's Go SDK does, via
// ``BIGQUERY_EMULATOR_HOST``; the Java side never adopted it — see
// the search for ``BIGQUERY_EMULATOR_HOST`` in apache/beam:
// only test files match, no production code). To redirect the Apiary
// ``Bigquery`` client at a local emulator we have to construct it
// ourselves with ``setRootUrl(...)`` and inject it via the
// documented ``BigQueryIO.Write.withTestServices(...)`` hook.
//
// The class is test-only: production callers should never touch
// ``BigQueryIO.Write.withTestServices`` (real BigQuery is reached via
// the default ``BigQueryServicesImpl.INSTANCE``).
package org.apache.beam.sdk.io.gcp.bigquery

import com.google.api.client.http.{HttpRequest, HttpRequestInitializer}
import com.google.api.services.bigquery.Bigquery
import org.apache.beam.sdk.extensions.gcp.util.Transport
import org.apache.beam.sdk.io.gcp.bigquery.BigQueryServices.{
  DatasetService,
  JobService,
}
import org.apache.beam.sdk.options.PipelineOptions

/** BigQueryServices that targets a local bqemulator REST surface.
  *
  * @param emulatorEndpoint
  *   the bqemulator's REST root URL (e.g. ``http://localhost:9050``).
  *   Trailing slash is normalised; the Apiary ``Bigquery`` client
  *   appends ``bigquery/v2/`` automatically.
  */
class EmulatorBigQueryServices(emulatorEndpoint: String)
    extends BigQueryServicesImpl
    with Serializable {

  // Normalise: Apiary's ``setRootUrl`` requires a trailing slash —
  // without it the constructed URLs are missing the boundary between
  // root and service path (e.g.
  // ``http://localhostbigquery/v2/projects/...``).
  private val normalisedEndpoint: String =
    if (emulatorEndpoint.endsWith("/")) emulatorEndpoint
    else emulatorEndpoint + "/"

  private def buildClient(): Bigquery = {
    // No auth header — the emulator accepts any caller, and adding an
    // Authorization header would only make us route through the OAuth
    // refresh path we're explicitly trying to avoid (see the
    // ``--gcpCredentialFactoryClass=NoopCredentialFactory`` flag the
    // pipeline passes in tandem).
    val noopInit: HttpRequestInitializer = new HttpRequestInitializer {
      override def initialize(request: HttpRequest): Unit = {
        request.setReadTimeout(120_000)
        request.setConnectTimeout(20_000)
      }
    }
    new Bigquery.Builder(
      Transport.getTransport,
      Transport.getJsonFactory,
      noopInit,
    )
      .setRootUrl(normalisedEndpoint)
      .setApplicationName("bqemu-scio-example")
      .build()
  }

  // The JobServiceImpl(@VisibleForTesting Bigquery client) constructor
  // is package-private; this file lives in the same package so the
  // reference resolves at compile time.
  override def getJobService(options: BigQueryOptions): JobService =
    new BigQueryServicesImpl.JobServiceImpl(buildClient())

  override def getDatasetService(
      options: BigQueryOptions,
  ): DatasetService =
    new BigQueryServicesImpl.DatasetServiceImpl(
      buildClient(),
      options.asInstanceOf[PipelineOptions],
    )
}
