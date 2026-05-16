# FinPilot AI — System Architecture Flowchart

This diagram outlines the complete end-to-end architecture of FinPilot AI, covering everything from user input routing to the specialized agents (Collector, Search, Advisor, Investor) and the hybrid retrieval pipeline.

```mermaid
flowchart TD
    User[Telegram User] --> Msg[Text Message]
    User --> Photo[Receipt Photo]
    User --> Doc[PDF Document]

    Doc -->|PyMuPDF Text Extract| Extractor[Data Extraction Engine]
    Photo -->|Gemini Multimodal| Extractor

    Msg --> Router{Global Intent Router}
    Extractor --> Router

    Router -->|Intent log/expense| Collector[Collector Agent Data Ingestion]
    Router -->|Intent search/query| Search[Search Agent Memory Retrieval]
    Router -->|Intent advice/budget| Advisor[Advisor Agent Stage 3]
    Router -->|Intent stocks/portfolio| Investor[Investor Agent Future Stage]

    Collector --> ParseCat{Parse and Categorize}
    
    ParseCat -->|Single Transaction| DBWrite[Write to PostgreSQL]
    ParseCat -->|Credit Card or Salary Slip| ParseMulti[Parse Multiple Transactions]
    ParseMulti --> DBWrite
    
    DBWrite --> AutoEmbed[Auto-Embed to Vector Store]
    AutoEmbed --> ChromaDB[(ChromaDB Semantic Memory)]

    Search --> IntentClass{Search Intent Classification}
    
    IntentClass -->|structured categories dates| SQLPath[SQL Query Exact Match]
    IntentClass -->|semantic vague queries| VectorPath[Vector Search Fuzzy Match]
    
    SQLPath -->|0 results and has keyword| VectorPath
    
    SQLPath --> FetchDB[Fetch Full Transactions]
    VectorPath --> FetchChroma[Query Top K Docs]
    FetchChroma --> FetchDB
    
    FetchDB --> PythonStats[Python Stats Computation Totals Categories]
    PythonStats --> Synthesis[Gemini Answer Synthesis]
    
    Synthesis --> Output[Chat Response]

    Advisor --> BudgetEngine[Budget and Limit Analysis]
    Investor --> MarketAPI[External Market API Integration]
    
    BudgetEngine --> Output
    MarketAPI --> Output

    Supabase[(Supabase PostgreSQL)]
    DBWrite -.->|Saves Transactions| Supabase
    FetchDB -.->|Reads Data| Supabase
```
