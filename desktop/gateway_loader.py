"""Offline windows do not load the model SDK until an explicit model action."""


class GatewayError(ValueError):
    def __init__(self,message,provenance=None):
        super().__init__(message)
        self.provenance=provenance or {}


def ModelGateway(*args,**kwargs):
    from desktop.gateway import ModelGateway as Gateway
    return Gateway(*args,**kwargs)
