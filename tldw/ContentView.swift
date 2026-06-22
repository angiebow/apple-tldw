//
//  ContentView.swift
//  tldw — Phase 1: Topic Segmentation PoC
//
//  Left pane: the raw timestamped transcript.
//  Right pane: the contiguous topic segments BERTopic produced.
//

import SwiftUI

struct ContentView: View {
    @State private var model = SegmentationViewModel()

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            HSplitView {
                transcriptPane
                    .frame(minWidth: 280)
                segmentsPane
                    .frame(minWidth: 360)
            }
        }
        .frame(minWidth: 760, minHeight: 480)
        .task { await model.checkHealth() }
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("tldw — Topic Segmentation")
                    .font(.headline)
                Text(model.transcript.title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            serverPill

            Button {
                Task { await model.runSegmentation() }
            } label: {
                Label("Segment", systemImage: "wand.and.stars")
            }
            .buttonStyle(.borderedProminent)
            .disabled(model.isLoading || model.transcript.utterances.isEmpty)
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

    // MARK: Transcript pane

    private var transcriptPane: some View {
        VStack(alignment: .leading, spacing: 0) {
            paneTitle("Transcript", subtitle: "\(model.transcript.utterances.count) utterances")
            List(model.transcript.utterances) { utt in
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(utt.start.asTimecode) – \(utt.end.asTimecode)")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                    Text(utt.text)
                        .font(.callout)
                }
                .padding(.vertical, 2)
            }
            .listStyle(.inset)
        }
    }

    // MARK: Segments pane

    private var segmentsPane: some View {
        VStack(alignment: .leading, spacing: 0) {
            paneTitle("Topic Segments", subtitle: model.statusMessage)

            if model.isLoading {
                Spacer()
                ProgressView("Running BERTopic…")
                    .frame(maxWidth: .infinity)
                Spacer()
            } else if model.segments.isEmpty {
                Spacer()
                ContentUnavailableView(
                    "No segments yet",
                    systemImage: "square.stack.3d.up.slash",
                    description: Text("Tap Segment to cluster the transcript into topics.")
                )
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 10) {
                        ForEach(model.segments) { segment in
                            SegmentCard(segment: segment, color: model.color(for: segment))
                        }
                    }
                    .padding()
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

// MARK: - Segment card

struct SegmentCard: View {
    let segment: TopicSegment
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(segment.label)
                    .font(.headline)
                    .foregroundStyle(segment.isOutlier ? .secondary : .primary)
                Spacer()
                Text("\(segment.start.asTimecode) – \(segment.end.asTimecode)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }

            if !segment.keywords.isEmpty {
                KeywordFlow(keywords: Array(segment.keywords.prefix(6)), color: color)
            }

            Text(segment.text)
                .font(.callout)
                .foregroundStyle(.secondary)
                .lineLimit(3)

            HStack(spacing: 12) {
                Label("\(Int(segment.duration.rounded()))s", systemImage: "clock")
                Label("\(segment.utteranceCount) lines", systemImage: "text.alignleft")
                if segment.isOutlier {
                    Label("outlier", systemImage: "questionmark.circle")
                } else {
                    Label("topic \(segment.topicId)", systemImage: "number")
                }
            }
            .font(.caption2)
            .foregroundStyle(.secondary)
        }
        .padding()
        .background(color.opacity(segment.isOutlier ? 0.04 : 0.10), in: RoundedRectangle(cornerRadius: 10))
        .overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 2)
                .fill(color)
                .frame(width: 4)
                .padding(.vertical, 6)
        }
    }
}

// MARK: - Simple wrapping chip layout

struct KeywordFlow: View {
    let keywords: [String]
    let color: Color

    var body: some View {
        FlowLayout(spacing: 6) {
            ForEach(keywords, id: \.self) { word in
                Text(word)
                    .font(.caption2)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(color.opacity(0.18), in: Capsule())
            }
        }
    }
}

/// Minimal flow layout so keyword chips wrap onto multiple lines.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x + size.width > maxWidth, x > 0 {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: maxWidth == .infinity ? x : maxWidth, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}

#Preview {
    ContentView()
}
