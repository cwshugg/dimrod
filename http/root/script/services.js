// This module defines the 'Service' class, a simple uniform way for my server
// to keep track of various services the home server provides.

// Global status "enum" for services.
const ServiceStatus =
{
    UNKNOWN: -1,
    DOWN: 0,
    UP: 1
}


// Main service class.
class Service
{
    // Constructor.
    constructor(name)
    {
        this.name = name;
        this.state = ServiceStatus.UNKNOWN;
    }
}

// Service status enum.

