// This module defines the 'Service' class, a simple uniform way for my server
// to keep track of various services the home server provides.

// Main service class.
class Service
{
    // Constructor. Takes in an ID, name, and a URL object.
    constructor(id, name, url)
    {
        this.id = id;
        this.name = name;
        this.url = url;
    }

    // =========================== HTML Elements ============================ //
    // Creates and returns an HTML anchor element that takes the user to the
    // running service.
    make_anchor(text)
    {
        const a = document.createElement("a");
        a.id = this.id + "_anchor";
        a.href = this.url.get_string();
        a.innerHTML = text;
        return a;
    }
}

