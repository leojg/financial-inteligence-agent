from dataclasses import dataclass, field

@dataclass
class ReconciliationConfig:
    model_name: str = "gpt-4o-mini"
    temperature: float = 0.0
    base_currency: str = "USD"
    categories: list[str] = field(default_factory=lambda: [
        "Groceries", "Dining", "Transport", "Utilities", "Healthcare",
        "Entertainment", "Shopping", "Travel", "Education", "Transfer",
        "Fees & Charges", "Salary", "Freelance", "Other Income", "Other"
    ])

DEFAULT_CONFIG = ReconciliationConfig()