//
//  SampleText.swift
//  tldw — Overall Summary (BART)
//
//  A sample passage so the app is usable the moment it launches.
//

enum SampleText {
    static let podcastExcerpt = """
    Welcome back to the show. Today we are going deep on large language models and how transformers actually learn. \
    The attention mechanism is really the core idea: every token attends to every other token in the sequence, and \
    that is what lets these neural networks capture long range context that older recurrent models struggled with. \
    When you scale the model parameters and the training data together, you start to see emergent reasoning abilities. \
    Fine tuning and reinforcement learning from human feedback then align the model to be helpful and to follow \
    instructions. The embeddings these models produce are also incredibly useful for search, clustering, and \
    retrieval augmented generation. It is remarkable that a single architecture now powers chatbots, code \
    assistants, and image captioning all at once. Later in the episode we shift to the business side, talking about \
    how a founder raised their seed round. The hardest part, they say, was the pitch deck: investors want to see \
    traction, a clear market, and a credible founding team. They spoke with roughly forty venture firms before \
    finding the right lead investor, and the valuation negotiations were brutal because you have to balance dilution \
    against the runway you need to hit the next milestone. Their advice to founders is to build relationships with \
    investors months before you actually need the capital, because once the term sheet is signed the real work of \
    scaling the company and hiring a team only just begins.
    """
}
