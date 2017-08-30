
preserve_types = [type(''), type(u''), type(0), type(True)]

def cleanup(data):
  if (type(data) == type({})):
    return {k: cleanup(data[k]) for k in data.keys()}
  elif (type(data) == type([])):
    return [cleanup(i) for i in data]
  elif type(data) in preserve_types:
    return data
  else:
    return None

