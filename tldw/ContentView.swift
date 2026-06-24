//
//  ContentView.swift
//  tldw — Overall Summary (BART) PoC
//
//  Left pane: text input.  Right pane: the BART summary.
//

import SwiftUI

struct ContentView: View {
    @State private var model = SummarizationViewModel()

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            HSplitView {
                inputPane
                    .frame(minWidth: 320)
                summaryPane
                    .frame(minWidth: 320)
            }
        }
        .frame(minWidth: 760, minHeight: 460)
        .task { await model.checkHealth() }
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("tldw — Overall Summary")
                    .font(.headline)
                Text("BART abstractive summarization")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            serverPill

            Button {
                model.loadSample()
            } label: {
                Label("Sample", systemImage: "doc.text")
            }
            .disabled(model.isLoading)

            Button {
                Task { await model.summarize() }
            } label: {
                Label("Summarize", systemImage: "sparkles")
            }
            .buttonStyle(.borderedProminent)
            .disabled(model.isLoading || model.inputText.trimmingCharacters(in: .whitespacesAndNewlines).count < 40)
        }
        .padding()
    }

    private var serverPill: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(serverColor)
                .frame(width: 8, height: 8)
            Text(serverLabel)
                .font(.caption)
                .foregroundStyle(.secondary)
            Button {
                Task { await model.checkHealth() }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .help("Re-check backend at 127.0.0.1:8000")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(.quaternary, in: Capsule())
    }

    private var serverColor: Color {
        switch model.serverReachable {
        case .some(true): return .green
        case .some(false): return .red
        case .none: return .yellow
        }
    }

    private var serverLabel: String {
        switch model.serverReachable {
        case .some(true): return "backend up"
        case .some(false): return "backend down"
        case .none: return "checking…"
        }
    }

    // MARK: Input pane

    private var inputPane: some View {
        VSplitView {
            VStack(alignment: .leading, spacing: 0) {
                paneTitle("Input text", subtitle: "\(model.inputText.count) characters")
                TextEditor(text: $model.inputText)
                    .font(.callout)
                    .padding(8)
                    .scrollContentBackground(.hidden)
            }

            VStack(alignment: .leading, spacing: 0) {
                paneTitle(
                    "Reference summary",
                    subtitle: "Optional — enables ROUGE / BERTScore / METEOR scoring"
                )
                TextEditor(text: $model.referenceText)
                    .font(.callout)
                    .padding(8)
                    .scrollContentBackground(.hidden)
            }
            .frame(minHeight: 80, idealHeight: 120)
        }
    }

    // MARK: Summary pane

    private var summaryPane: some View {
        VStack(alignment: .leading, spacing: 0) {
            paneTitle("Summary", subtitle: model.stats.isEmpty ? model.statusMessage : model.stats)

            if model.isLoading {
                Spacer()
                ProgressView("Summarizing with BART…")
                    .frame(maxWidth: .infinity)
                Spacer()
            } else if model.summary.isEmpty {
                Spacer()
                ContentUnavailableView(
                    "No summary yet",
                    systemImage: "sparkles",
                    description: Text("Tap Summarize to condense the text with BART.")
                )
                Spacer()
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        Text(model.summary)
                            .font(.body)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)

                        if let metrics = model.metrics {
                            metricsCard(metrics)
                        }
                    }
                    .padding()
                }
            }
        }
    }

    // MARK: Evaluation metrics

    private func metricsCard(_ m: SummaryMetrics) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Evaluation vs. reference", systemImage: "checklist")
                .font(.subheadline.bold())

            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                metricRow("ROUGE", [
                    ("R-1", m.rouge.rouge1),
                    ("R-2", m.rouge.rouge2),
                    ("R-L", m.rouge.rougeL),
                ])
                metricRow("BERTScore", [
                    ("P", m.bertscore.precision),
                    ("R", m.bertscore.recall),
                    ("F1", m.bertscore.f1),
                ])
                metricRow("METEOR", [("", m.meteor)])
            }

            Text("Higher is better · 0–1 scale")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
    }

    private func metricRow(_ title: String, _ values: [(String, Double)]) -> some View {
        GridRow {
            Text(title)
                .font(.caption.bold())
                .gridColumnAlignment(.leading)
            HStack(spacing: 8) {
                ForEach(values, id: \.0) { sub in
                    HStack(spacing: 4) {
                        if !sub.0.isEmpty {
                            Text(sub.0)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        Text(String(format: "%.2f", sub.1))
                            .font(.caption.monospacedDigit())
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.secondary.opacity(0.15), in: Capsule())
                }
            }
        }
    }

    private func paneTitle(_ title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.subheadline.bold())
            Text(subtitle)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.bar)
    }
}

#Preview {
    ContentView()
}
