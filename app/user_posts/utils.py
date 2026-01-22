import h3

def compute_cluster_id(lat, lng, resolution):
    return h3.latlng_to_cell(lat, lng, resolution)
