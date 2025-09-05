def add_method(cls):
    def decorator(func):
        setattr(cls, func.__name__, func)
    return decorator