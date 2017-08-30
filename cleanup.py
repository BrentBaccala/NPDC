
preserve_types = [type(''), type(u''), type(0), type(True)]

def cleanup(data):
  if (type(data) == type({})):
    for k in data.keys():
      data[k] = cleanup(data[k])
    return data
  elif (type(data) == type([])):
    return [cleanup(i) for i in data]
  elif type(data) in preserve_types:
    return data
  else:
    return None

