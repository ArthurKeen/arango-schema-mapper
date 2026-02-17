class SchemaAnalyzerError(Exception):
    def __init__(self, message: str, code: str | None = None, *, cause: Exception | None = None):
        super().__init__(message)
        self.code = code
        self.__cause__ = cause

