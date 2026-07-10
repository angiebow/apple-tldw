//
//  Theme.swift
//  tldw — ViReel design system
//
//  A dark, cinematic glassmorphism look: dusky gradient ground, frosted
//  translucent panels (`.ultraThinMaterial`), continuous rounded cards, and a
//  warm-coral → periwinkle accent. Shared tokens + view modifiers + button
//  styles so every screen reads as one system.
//

import SwiftUI

// MARK: - Tokens

enum ViReel {
    // Accent — warm coral into a soft periwinkle violet (the dusky-sky palette).
    static let accentWarm = Color(red: 0.98, green: 0.52, blue: 0.40)
    static let accentCool = Color(red: 0.60, green: 0.55, blue: 0.96)

    static let accentGradient = LinearGradient(
        colors: [accentWarm, accentCool],
        startPoint: .topLeading, endPoint: .bottomTrailing)

    /// Hairline stroke that reads on both frosted-light and frosted-dark glass.
    static let hairline = Color.white.opacity(0.14)

    // Corner radii — cards are generously rounded like the reference.
    static let rCard: CGFloat = 22
    static let rPanel: CGFloat = 18
    static let rControl: CGFloat = 12
}

// MARK: - Background

/// The app's dusky gradient ground. Dark mode is the showcase (deep plum/navy
/// with warm ember + cool glows); light mode is a soft warm-lilac variant so the
/// frosted panels still read. Placed once behind every screen.
struct AppBackground: View {
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        ZStack {
            base
            // Warm ember glow, lower-right (the sunset in the reference).
            RadialGradient(colors: [ViReel.accentWarm.opacity(scheme == .dark ? 0.28 : 0.18), .clear],
                           center: .init(x: 0.85, y: 0.9), startRadius: 10, endRadius: 720)
            // Cool violet glow, upper-left.
            RadialGradient(colors: [ViReel.accentCool.opacity(scheme == .dark ? 0.26 : 0.16), .clear],
                           center: .init(x: 0.1, y: 0.05), startRadius: 10, endRadius: 680)
        }
        .ignoresSafeArea()
    }

    private var base: some View {
        LinearGradient(
            colors: scheme == .dark
                ? [Color(red: 0.055, green: 0.06, blue: 0.10),
                   Color(red: 0.09, green: 0.08, blue: 0.14),
                   Color(red: 0.06, green: 0.07, blue: 0.11)]
                : [Color(red: 0.96, green: 0.95, blue: 0.98),
                   Color(red: 0.94, green: 0.93, blue: 0.97),
                   Color(red: 0.97, green: 0.95, blue: 0.96)],
            startPoint: .top, endPoint: .bottom)
    }
}

// MARK: - Glass modifiers

private struct GlassPanel: ViewModifier {
    var cornerRadius: CGFloat
    var strokeOpacity: Double
    var shadowRadius: CGFloat

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        return content
            .background(.ultraThinMaterial, in: shape)
            .overlay(shape.strokeBorder(Color.white.opacity(strokeOpacity), lineWidth: 1))
            .shadow(color: .black.opacity(0.28), radius: shadowRadius, y: shadowRadius * 0.45)
    }
}

extension View {
    /// Frosted translucent panel — the core glassmorphism surface.
    func glassPanel(cornerRadius: CGFloat = ViReel.rPanel,
                    strokeOpacity: Double = 0.14,
                    shadowRadius: CGFloat = 18) -> some View {
        modifier(GlassPanel(cornerRadius: cornerRadius,
                            strokeOpacity: strokeOpacity, shadowRadius: shadowRadius))
    }

    /// A card surface — a slightly larger radius glass panel.
    func glassCard() -> some View { glassPanel(cornerRadius: ViReel.rCard, shadowRadius: 22) }
}

// MARK: - Button styles

/// Prominent pill CTA with the accent gradient — the reference's "Start" button.
struct PillButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.callout.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 20).padding(.vertical, 11)
            .background(ViReel.accentGradient, in: Capsule())
            .overlay(Capsule().strokeBorder(Color.white.opacity(0.18), lineWidth: 1))
            .shadow(color: ViReel.accentWarm.opacity(0.35), radius: 12, y: 5)
            .opacity(configuration.isPressed ? 0.85 : 1)
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

/// Frosted secondary pill — the reference's "Guidebook" button.
struct GlassPillButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.callout.weight(.medium))
            .padding(.horizontal, 18).padding(.vertical, 11)
            .background(.ultraThinMaterial, in: Capsule())
            .overlay(Capsule().strokeBorder(Color.white.opacity(0.16), lineWidth: 1))
            .opacity(configuration.isPressed ? 0.8 : 1)
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

extension ButtonStyle where Self == PillButtonStyle {
    static var pill: PillButtonStyle { PillButtonStyle() }
}
extension ButtonStyle where Self == GlassPillButtonStyle {
    static var glassPill: GlassPillButtonStyle { GlassPillButtonStyle() }
}
