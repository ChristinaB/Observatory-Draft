# Import python modules
import os, sys

# data handling libraries
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pickle
import json
import dask
from multiprocessing import Pool

# graphical control libraries
import matplotlib as mpl
mpl.use('agg')
import matplotlib.pyplot as plt

# shape and layer libraries
import fiona
from shapely.geometry import MultiPolygon, shape, point, box
from descartes import PolygonPatch
from matplotlib.collections import PatchCollection
from mpl_toolkits.basemap import Basemap
from mpl_toolkits.axes_grid1 import make_axes_locatable
import geopandas as gpd

# data wrangling libraries
# import urllib2
import ftplib, wget, bz2
from bs4 import BeautifulSoup as bs


class ogh_meta:
    """
    The json file that describes the Climate data product resources
    """
    def __init__(self):
        self.__meta_data = dict(json.load(open('ogh_meta.json','rb')))
    
    def keys(self):
        return(self.__meta_data.keys())
    
    def values(self):
        return(self.__meta_data.values())
    
    def __getitem__(self, key):
        return(self.__meta_data[key])
               

# print('Version '+datetime.fromtimestamp(os.path.getmtime('ogh.py')).strftime('%Y-%m-%d %H:%M:%S')+' jp')

def saveDictOfDf(outfilename, dictionaryObject):
    # write a dictionary of dataframes to a json file using pickle
    with open(outfilename, 'wb') as f:
        pickle.dump(dictionaryObject, f)
        f.close()

def readDictOfDf(infilename):
    # read a dictionary of dataframes from a json file using pickle
    with open(infilename, 'rb') as f:
        dictionaryObject = pickle.load(f)
        f.close()
    return(dictionaryObject)

def reprojShapefile(sourcepath, newprojdictionary={'proj':'longlat', 'ellps':'WGS84', 'datum':'WGS84'}, outpath=None):
    """
    sourcepath: (dir) the path to the .shp file
    newprojdictionary: (dict) the new projection definition in the form of a dictionary (default provided)
    outpath: (dir) the output path for the new shapefile
    """
    
    # if outpath is none, treat the reprojection as a file replacement
    if isinstance(outpath, type(None)):
        outpath = sourcepath
        
    shpfile = gpd.GeoDataFrame.from_file(sourcepath)
    shpfile = shpfile.to_crs(newprojdictionary)
    shpfile.to_file(outpath)


def getFullShape(shapefile):
    """
    Generate a MultiPolygon to represent each shape/polygon within the shapefile
    
    shapefile: (dir) a path to the ESRI .shp shapefile
    """
    shp = fiona.open(shapefile)
    mp = [shape(pol['geometry']) for pol in shp]
    mp = MultiPolygon(mp)
    shp.close()
    return(mp)
    
    
def getShapeBbox(polygon):
    """
    Generate a geometric box to represent the bounding box for the polygon, shapefile connection, or MultiPolygon
    
    polygon: (geometry) a geometric polygon, MultiPolygon, or shapefile connection
    """
    # identify the cardinal bounds
    minx, miny, maxx, maxy = polygon.bounds
    bbox = box(minx, miny, maxx, maxy, ccw=True)
    return(bbox)


def readShapefileTable(shapefile):
    """
    read in the datatable captured within the shapefile properties
    
    shapefile: (dir) a path to the ESRI .shp shapefile
    """
    #cent_df = gpd.read_file(shapefile)
    shp = fiona.open(shapefile)
    centroid = [eachpol['properties'] for eachpol in shp]
    cent_df = pd.DataFrame.from_dict(centroid, orient='columns')
    shp.close()
    return(cent_df)


def filterPointsinShape(shape, points_lat, points_lon, points_elev=None, buffer_distance=0.06, buffer_resolution=16, 
                        labels=['LAT', 'LONG_', 'ELEV']):
    """
    filter for datafiles that can be used
    
    shape: (geometry) a geometric polygon or MultiPolygon
    points_lat: (series) a series of latitude points in WGS84 projection
    points_lon: (series) a series of longitude points in WGS84 projection
    points_elev: (series) a series of elevation points in meters; optional - default is None
    buffer_distance: (float64) a numerical multiplier to increase the geodetic boundary area
    buffer_resolution: (float64) the increments between geodetic longlat degrees
    labels: (list) a list of preferred labels for latitude, longitude, and elevation
    """
    # add buffer region
    region = shape.buffer(buffer_distance, resolution=buffer_resolution)
    
    # construct points_elev if null
    if isinstance(points_elev, type(None)):
        points_elev=np.repeat(np.nan, len(points_lon))
        
    # Intersection each coordinate with the region
    limited_list = []
    for lon, lat, elev in zip(points_lon, points_lat, points_elev):
        gpoint = point.Point(lon, lat)
        if gpoint.intersects(region):
            limited_list.append([lat, lon, elev])
    maptable = pd.DataFrame.from_records(limited_list, columns=labels)
    
    ## dask approach ##
    #intersection=[]
    #for lon, lat, elev in zip(points_lon, points_lat, points_elev):
    #    gpoint = point.Point(lon, lat)
    #    intersection.append(dask.delayed(gpoint.intersects(region)))
    #    limited_list.append([intersection, lat, lon, elev])
    # convert to dataframe
    #maptable = pd.DataFrame({labels[0]:points_lat, labels[1]:points_lon, labels[2]:points_elev}
    #                        .loc[dask.compute(intersection)[0],:]
    #                        .reset_index(drop=True)
    return(maptable)


def scrapeurl(url, startswith=None, hasKeyword=None):
    """
    scrape the gridded datafiles from a url of interest
    
    url: (str) the web folder path to be scraped for hyperlink references
    startswith: (str) the starting keywords for a webpage element; default is None
    hasKeyword: (str) keywords represented in a webpage element; default is None
    """
    # grab the html of the url, and prettify the html structure
    page = urllib2.urlopen(url).read()
    page_soup = bs(page, 'lxml')
    page_soup.prettify()

    # loop through and filter the hyperlinked lines
    if pd.isnull(startswith):
        temp = [anchor['href'] for anchor in page_soup.findAll('a', href=True) if hasKeyword in anchor['href']]
    else:
        temp = [anchor['href'] for anchor in page_soup.findAll('a', href=True) if anchor['href'].startswith(startswith)]

    # convert to dataframe then separate the lon and lat as float coordinate values
    temp = pd.DataFrame(temp, columns = ['filenames'])
    return(temp)


def treatgeoself(shapefile, NAmer, folder_path=os.getcwd(), outfilename='mappingfile.csv', buffer_distance=0.06):
    """
    TreatGeoSelf to some [data] lovin'!
    
    shapefile: (dir) the path to an ESRI shapefile for the region of interest
    Namer: (dir) the path to an ESRI shapefile, which has each 1/16th coordinate and elevation information from a DEM
    folder_path: (dir) the destination folder path for the mappingfile output; default is the current working directory
    outfilename: (str) the name of the output file; default name is 'mappingfile.csv'
    buffer_distance: (float64) the multiplier to be applied for increasing the geodetic boundary area; default is 0.06
    """
    # conform projections to longlat values in WGS84
    reprojShapefile(shapefile, newprojdictionary={'proj':'longlat', 'ellps':'WGS84', 'datum':'WGS84'}, outpath=None)
    
    # read shapefile into a multipolygon shape-object
    shape_mp = getFullShape(shapefile)

    # read in the North American continental DEM points for the station elevations
    NAmer_datapoints = readShapefileTable(NAmer).rename(columns={'Lat':'LAT','Long':'LONG_','Elev':'ELEV'})
    
    # generate maptable
    maptable = filterPointsinShape(shape_mp,
                                   points_lat=NAmer_datapoints.LAT,
                                   points_lon=NAmer_datapoints.LONG_,
                                   points_elev=NAmer_datapoints.ELEV,
                                   buffer_distance=buffer_distance, buffer_resolution=16, labels=['LAT', 'LONG_', 'ELEV'])
    maptable.reset_index(inplace=True)
    maptable = maptable.rename(columns={"index":"FID"})
    print(maptable.shape)
    print(maptable.tail())
    
    # print the mappingfile
    mappingfile=os.path.join(folder_path, outfilename)
    maptable.to_csv(mappingfile, sep=',', header=True, index=False)
    return(mappingfile)


def mapContentFolder(resid):
    """
    map the content folder within HydroShare
    
    resid: (str) a string hash that represents the hydroshare resource that has been migrated
    """
    path = os.path.join('/home/jovyan/work/notebooks/data', str(resid), str(resid), 'data/contents')
    return(path)


# ### CIG (DHSVM)-oriented functions


def compile_bc_Livneh2013_locations(maptable):
    """
    compile a list of file URLs for bias corrected Livneh et al. 2013 (CIG)

    maptable: (dataframe) a dataframe that contains the FID, LAT, LONG_, and ELEV for each interpolated data file
    """
    locations=[]
    for ind, row in maptable.iterrows():
        basename='_'.join(['data', str(row['LAT']), str(row['LONG_'])])
        url=['http://cses.washington.edu/rocinante/Livneh/bcLivneh_WWA_2013/forcings_ascii/',basename]
        locations.append(''.join(url))
    return(locations)


def compile_Livneh2013_locations(maptable):
    """
    compile a list of file URLs for Livneh et al. 2013 (CIG)
    
    maptable: (dataframe) a dataframe that contains the FID, LAT, LONG_, and ELEV for each interpolated data file
    """
    locations=[]
    for ind, row in maptable.iterrows():
        basename='_'.join(['data', str(row['LAT']), str(row['LONG_'])])
        url=['http://www.cses.washington.edu/rocinante/Livneh/Livneh_WWA_2013/forcs_dhsvm/',basename]
        locations.append(''.join(url))
    return(locations)


### VIC-oriented functions


def compile_VICASCII_Livneh2015_locations(maptable):
    """
    compile the list of file URLs for Livneh et al., 2015 VIC.ASCII outputs
    
    maptable: (dataframe) a dataframe that contains the FID, LAT, LONG_, and ELEV for each interpolated data file
    """
    locations=[]
    for ind, row in maptable.iterrows():
        loci='_'.join(['Fluxes_Livneh_NAmerExt_15Oct2014', str(row['LAT']), str(row['LONG_'])])
        url=["ftp://192.12.137.7/pub/dcp/archive/OBS/livneh2014.1_16deg/VIC.ASCII/latitude.",str(row['LAT']),'/',loci,'.bz2']
        locations.append(''.join(url))
    return(locations)


def compile_VICASCII_Livneh2013_locations(maptable):
    """
    compile the list of file URLs for Livneh et al., 2013 VIC.ASCII outputs for the USA
    
    maptable: (dataframe) a dataframe that contains the FID, LAT, LONG_, and ELEV for each interpolated data file
    """
    # identify the subfolder blocks
    blocks = scrape_domain(domain='livnehpublicstorage.colorado.edu',
                           subdomain='/public/Livneh.2013.CONUS.Dataset/Fluxes.asc.v.1.2.1915.2011.bz2/',
                           startswith='fluxes')
    
    # map each coordinate to the subfolder
    maptable = mapToBlock(maptable, blocks)

    locations=[]
    for ind, row in maptable.iterrows():
        loci='_'.join(['VIC_fluxes_Livneh_CONUSExt_v.1.2_2013', str(row['LAT']), str(row['LONG_'])])
        url='/'.join(["ftp://livnehpublicstorage.colorado.edu/public/Livneh.2013.CONUS.Dataset/Fluxes.asc.v.1.2.1915.2011.bz2", str(row['block']), loci+".bz2"])
        locations.append(url)
    return(locations)


### Climate (Meteorological observations)-oriented functions

def canadabox_bc():
    """
    Establish the Canadian (north of the US bounding boxes) Columbia river basin bounding box
    """
    # left, bottom, right top
    return(box(-138.0, 49.0, -114.0, 53.0))

def scrape_domain(domain, subdomain, startswith=None):
    """
    scrape the gridded datafiles from a url of interest
    
    domain: (str) the web folder path
    subdomain: (str) the subfolder path to be scraped for hyperlink references
    startswith: (str) the starting keywords for a webpage element; default is None
    """
    # connect to domain
    ftp = ftplib.FTP(domain)
    ftp.login()
    ftp.cwd(subdomain)
    
    # scrape for data directories
    tmp = [dirname for dirname in ftp.nlst() if dirname.startswith(startswith)]
    geodf = pd.DataFrame(tmp, columns=['dirname'])
    
    # conform to bounding box format
    tmp = geodf['dirname'].apply(lambda x: x.split('.')[1:])
    tmp = tmp.apply(lambda x: list(map(float,x)) if len(x)>2 else x)

    # assemble the boxes
    geodf['bbox']=tmp.apply(lambda x: box(x[0]*-1, x[2]-1, x[1]*-1, x[3]) if len(x)>2 else canadabox_bc())
    return(geodf)


def mapToBlock(df_points, df_regions):
    
    for index, eachblock in df_regions.iterrows():
        for ind, row in df_points.iterrows():
            if point.Point(row['LONG_'], row['LAT']).intersects(eachblock['bbox']):
                df_points.loc[ind, 'block'] = str(eachblock['dirname'])
    return(df_points)


def compile_dailyMET_Livneh2013_locations(maptable):
    """
    compile the list of file URLs for Livneh et al., 2013 Daily Meteorology data
    
    maptable: (dataframe) a dataframe that contains the FID, LAT, LONG_, and ELEV for each interpolated data file
    """
    # identify the subfolder blocks
    blocks = scrape_domain(domain='livnehpublicstorage.colorado.edu',
                           subdomain='/public/Livneh.2013.CONUS.Dataset/Meteorology.asc.v.1.2.1915.2011.bz2/',
                           startswith='data')
        
    # map each coordinate to the subfolder
    maptable = mapToBlock(maptable, blocks)

    locations=[]
    for ind, row in maptable.iterrows():
        loci='_'.join(['Meteorology_Livneh_CONUSExt_v.1.2_2013', str(row['LAT']), str(row['LONG_'])])
        url='/'.join(["ftp://livnehpublicstorage.colorado.edu/public/Livneh.2013.CONUS.Dataset/Meteorology.asc.v.1.2.1915.2011.bz2", str(row['block']), loci+".bz2"])
        locations.append(url)
    return(locations)


def compile_dailyMET_Livneh2015_locations(maptable):
    """
    compile the list of file URLs for Livneh et al., 2015 Daily Meteorology data
    
    maptable: (dataframe) a dataframe that contains the FID, LAT, LONG_, and ELEV for each interpolated data file
    """
    locations=[]
    for ind, row in maptable.iterrows():
        loci='_'.join(['Meteorology_Livneh_NAmerExt_15Oct2014', str(row['LAT']), str(row['LONG_'])])
        url=["ftp://192.12.137.7/pub/dcp/archive/OBS/livneh2014.1_16deg/ascii/daily/latitude.", str(row['LAT']),"/",loci,".bz2"]
        locations.append(''.join(url))
    return(locations)


# ### WRF-oriented functions


def compile_wrfnnrp_raw_Salathe2014_locations(maptable):
    """
    compile a list of file URLs for Salathe et al., 2014 raw WRF NNRP data
    
    maptable: (dataframe) a dataframe that contains the FID, LAT, LONG_, and ELEV for each interpolated data file
    """
    locations=[]
    for ind, row in maptable.iterrows():
        basename='_'.join(['data', str(row['LAT']), str(row['LONG_'])])
        url=['http://cses.washington.edu/rocinante/WRF/NNRP/vic_16d/WWA_1950_2010/raw/forcings_ascii/',basename]
        locations.append(''.join(url))
    return(locations)


def compile_wrfnnrp_bc_Salathe2014_locations(maptable):
    """
    compile a list of file URLs for the Salathe et al., 2014 bias corrected WRF NNRP data
    
    maptable: (dataframe) a dataframe that contains the FID, LAT, LONG_, and ELEV for each interpolated data file
    """
    locations=[]
    for ind, row in maptable.iterrows():
        basename='_'.join(['data', str(row['LAT']), str(row['LONG_'])])
        url=['http://cses.washington.edu/rocinante/WRF/NNRP/vic_16d/WWA_1950_2010/bc/forcings_ascii/',basename]
        locations.append(''.join(url))
    return(locations)


# ## Data file migration functions


def ensure_dir(f):
    """
    check if the destination folder directory exists; if not, create it and set it as the working directory
    
    f: (dir) the directory to create and/or set as working directory
    """
    if not os.path.exists(f):
        os.makedirs(f)
    os.chdir(f)


def wget_download(listofinterest):
    """
    Download files from an http domain
    
    listofinterest: (list) a list of urls to request
    """
    # check and download each location point, if it doesn't already exist in the download directory
    for fileurl in listofinterest:
        basename = os.path.basename(fileurl)
        try:
            wget.download(fileurl)
            print('downloaded: ' + basename)
        except:
            print('File does not exist at this URL: ' + basename)

# Download the files to the subdirectory
def wget_download_one(fileurl):
    """
    Download a file from an http domain
    
    fileurl: (url) a url to request
    """
    # check and download each location point, if it doesn't already exist in the download directory
    basename=os.path.basename(fileurl)
    
    # if it exists, remove for new download (overwrite mode)
    if os.path.isfile(basename):
        os.remove(basename)
        
    try:
        wget.download(fileurl)
        print('downloaded: ' + basename)
    except:
        print('File does not exist at this URL: ' + basename)
    
def wget_download_p(listofinterest, nworkers=20):
    """
    Download files from an http domain in parallel
    
    listofinterest: (list) a list of urls to request
    nworkers: (int) the number of processors to distribute tasks; default is 10
    """
    pool = Pool(int(nworkers))
    pool.map(wget_download_one, listofinterest)
    pool.close()
    pool.terminate()


def ftp_download(listofinterest):
    """
    Download and decompress files from an ftp domain
    
    listofinterest: (list) a list of urls to request
    """
    for loci in listofinterest:
        
        # establish path info
        fileurl=loci.replace('ftp://','') # loci is already the url with the domain already appended
        ipaddress=fileurl.split('/',1)[0] # ip address
        path=os.path.dirname(fileurl.split('/',1)[1]) # folder path
        filename=os.path.basename(fileurl) # filename
        
        # download the file from the ftp server
        ftp=ftplib.FTP(ipaddress)
        ftp.login()
        ftp.cwd(path)
        try:
            ftp.retrbinary("RETR " + filename ,open(filename, 'wb').write)
            ftp.close()
            
            # decompress the file
            decompbz2(filename)
        except:
            os.remove(filename)
            print('File does not exist at this URL: '+fileurl)
        
        
def ftp_download_one(loci):
    """
    Download and decompress a file from an ftp domain
    
    loci: (url) a url to request
    """
    # establish path info
    fileurl=loci.replace('ftp://','') # loci is already the url with the domain already appended
    ipaddress=fileurl.split('/',1)[0] # ip address
    path=os.path.dirname(fileurl.split('/',1)[1]) # folder path
    filename=os.path.basename(fileurl) # filename
        
    # download the file from the ftp server
    ftp=ftplib.FTP(ipaddress)
    ftp.login()
    ftp.cwd(path)
    try:
        ftp.retrbinary("RETR " + filename ,open(filename, 'wb').write)
        ftp.close()
        
        # decompress the file
        decompbz2(filename)
    except:
        os.remove(filename)
        print('File does not exist at this URL: '+fileurl)

        
def ftp_download_p(listofinterest, nworkers=5):
    """
    Download and decompress files from an ftp domain in parallel
    
    listofinterest: (list) a list of urls to request
    nworkers: (int) the number of processors to distribute tasks; default is 5
    """
    pool = Pool(int(nworkers))
    pool.map(ftp_download_one, listofinterest)
    pool.close()
    pool.terminate()
    

def decompbz2(filename):
    """
    Extract a file from a bz2 file of the same name, then remove the bz2 file
    
    filename: (dir) the file path for a bz2 compressed file
    """
    with open(filename.split(".bz2",1)[0], 'wb') as new_file, open(filename, 'rb') as zipfile:
        decompressor = bz2.BZ2Decompressor()
        for data in iter(lambda : zipfile.read(100 * 1024), b''):
            new_file.write(decompressor.decompress(data))
    os.remove(filename)
    zipfile.close()
    new_file.close()
    print(os.path.splitext(filename)[0] + ' unzipped')


def catalogfiles(folderpath):
    """
    make a catalog of the gridded files within a folderpath
    
    folderpath: (dir) the folder of files to be catalogged, which have LAT and LONG_ as the last two filename features
    """
    # read in downloaded files
    temp = [eachfile for eachfile in os.listdir(folderpath) if not os.path.isdir(eachfile)]
    if len(temp)==0:
        # no files were available; setting default catalog output structure
        catalog = pd.DataFrame([], columns=['filenames','LAT','LONG_'])
    else:
        # create the catalog dataframe and extract the filename components
        catalog = pd.DataFrame(temp, columns=['filenames'])
        catalog[['LAT','LONG_']] = catalog['filenames'].apply(lambda x: pd.Series(str(x).rsplit('_',2))[1:3]).astype(float)

        # convert the filenames column to a filepath
        catalog['filenames'] = catalog['filenames'].apply(lambda x: os.path.join(folderpath, x))
    return(catalog)


def addCatalogToMap(outfilepath, maptable, folderpath, catalog_label):
    """
    Update the mappingfile with a new column, a vector of filepaths for the downloaded files
    
    outfilepath: (dir) the path for the output file
    maptable: (dataframe) a dataframe containing the FID, LAT, LONG_, and ELEV information
    folderpath: (dir) the folder of files to be catalogged, which have LAT and LONG_ as the last two filename features
    catalog_label: (str) the preferred name for the series of catalogged filepaths
    """
    
    # assert catalog_label as a string-object
    catalog_label = str(catalog_label)
    
    # catalog the folder directory
    catalog = catalogfiles(folderpath).rename(columns={'filenames':catalog_label})
    
    # drop existing column and update with a vector for the catalog of files
    if catalog_label in maptable.columns:
        maptable = maptable.drop(labels=catalog_label, axis=1)
    maptable = maptable.merge(catalog, on=['LAT','LONG_'], how='left')
    
    # remove blocks, if they were needed
    if 'blocks' in maptable.columns:
        maptable = maptable.drop(labels=['blocks'], axis=1)
    
    # write the updated mappingfile
    maptable.to_csv(outfilepath, header=True, index=False)

    
# Wrapper scripts


def getDailyMET_livneh2013(homedir, mappingfile, subdir='livneh2013/Daily_MET_1915_2011/raw', catalog_label='dailymet_livneh2013'):
    """
    Get the Livneh el al., 2013 Daily Meteorology files of interest using the reference mapping file
    
    homedir: (dir) the home directory to be used for establishing subdirectories
    mappingfile: (dir) the file path to the mappingfile, which contains the LAT, LONG_, and ELEV coordinates of interest
    subdir: (dir) the subdirectory to be established under homedir
    catalog_label: (str) the preferred name for the series of catalogged filepaths
    """
    # check and generate DailyMET livneh 2013 data directory
    filedir=os.path.join(homedir, subdir)
    ensure_dir(filedir)
    
    # generate table of lats and long coordinates
    maptable = pd.read_csv(mappingfile)
    
    # compile the longitude and latitude points
    locations = compile_dailyMET_Livneh2013_locations(maptable)

    # Download the files
    ftp_download_p(locations)
    
    # update the mappingfile with the file catalog
    addCatalogToMap(outfilepath=mappingfile, maptable=maptable, folderpath=filedir, catalog_label=catalog_label)

    # return to the home directory
    os.chdir(homedir)
    return(filedir)


def getDailyMET_livneh2015(homedir, mappingfile, subdir='livneh2015/Daily_MET_1950_2013/raw', catalog_label='dailymet_livneh2015'):
    """
    Get the Livneh el al., 2015 Daily Meteorology files of interest using the reference mapping file
    
    homedir: (dir) the home directory to be used for establishing subdirectories
    mappingfile: (dir) the file path to the mappingfile, which contains the LAT, LONG_, and ELEV coordinates of interest
    subdir: (dir) the subdirectory to be established under homedir
    catalog_label: (str) the preferred name for the series of catalogged filepaths
    """
    # check and generate Daily MET livneh 2015 data directory
    filedir=os.path.join(homedir, subdir)
    ensure_dir(filedir)
    
    # generate table of lats and long coordinates
    maptable = pd.read_csv(mappingfile)
    
    # compile the longitude and latitude points
    locations = compile_dailyMET_Livneh2015_locations(maptable)
    
    # Download the files
    ftp_download_p(locations)
    
    # update the mappingfile with the file catalog
    addCatalogToMap(outfilepath=mappingfile, maptable=maptable, folderpath=filedir, catalog_label=catalog_label)
    
    # return to the home directory
    os.chdir(homedir)
    return(filedir)


def getDailyMET_bcLivneh2013(homedir, mappingfile, subdir='livneh2013/Daily_MET_1915_2011/bc', catalog_label='dailymet_bclivneh2013'):
    """
    Get the Livneh el al., 2013 bias corrected Daily Meteorology files of interest using the reference mapping file
    
    homedir: (dir) the home directory to be used for establishing subdirectories
    mappingfile: (dir) the file path to the mappingfile, which contains the LAT, LONG_, and ELEV coordinates of interest
    subdir: (dir) the subdirectory to be established under homedir
    catalog_label: (str) the preferred name for the series of catalogged filepaths
    """
    # check and generate baseline_corrected livneh 2013 data directory
    filedir=os.path.join(homedir, subdir)
    ensure_dir(filedir)
    
    # generate table of lats and long coordinates
    maptable = pd.read_csv(mappingfile)
    
    # compile the longitude and latitude points
    locations = compile_bc_Livneh2013_locations(maptable)

    # download the files
    wget_download_p(locations)

    # update the mappingfile with the file catalog
    addCatalogToMap(outfilepath=mappingfile, maptable=maptable, folderpath=filedir, catalog_label=catalog_label)
    
    # return to the home directory
    os.chdir(homedir)
    return(filedir)


def getDailyVIC_livneh2013(homedir, mappingfile, subdir='livneh2013/Daily_VIC_1915_2011', catalog_label='dailyvic_livneh2013'):
    """
    Get the Livneh el al., 2013 Daily VIC files of interest using the reference mapping file
    
    homedir: (dir) the home directory to be used for establishing subdirectories
    mappingfile: (dir) the file path to the mappingfile, which contains the LAT, LONG_, and ELEV coordinates of interest
    subdir: (dir) the subdirectory to be established under homedir
    catalog_label: (str) the preferred name for the series of catalogged filepaths
    """
    # FIRST RUN
    # check and generate VIC_ASCII Flux model livneh 2013 data directory
    filedir=os.path.join(homedir, subdir)
    ensure_dir(filedir)
    
    # generate table of lats and long coordinates
    maptable = pd.read_csv(mappingfile)

    # compile the longitude and latitude points for USA
    locations = compile_VICASCII_Livneh2013_locations(maptable)

    # Download the files
    ftp_download_p(locations)
    
    # update the mappingfile with the file catalog
    addCatalogToMap(outfilepath=mappingfile, maptable=maptable, folderpath=filedir, catalog_label=catalog_label)
    
    # return to the home directory
    os.chdir(homedir)
    return(filedir)


def getDailyVIC_livneh2015(homedir, mappingfile, subdir='livneh2015/Daily_VIC_1950_2013', catalog_label='dailyvic_livneh2015'):
    """
    Get the Livneh el al., 2015 Daily VIC files of interest using the reference mapping file
    
    homedir: (dir) the home directory to be used for establishing subdirectories
    mappingfile: (dir) the file path to the mappingfile, which contains the LAT, LONG_, and ELEV coordinates of interest
    subdir: (dir) the subdirectory to be established under homedir
    catalog_label: (str) the preferred name for the series of catalogged filepaths
    """
    # check and generate Daily VIC.ASCII Flux model livneh 2015 data directory
    filedir=os.path.join(homedir, subdir)
    ensure_dir(filedir)
    
    # generate table of lats and long coordinates
    maptable = pd.read_csv(mappingfile)
    
    # compile the longitude and latitude points
    locations = compile_VICASCII_Livneh2015_locations(maptable)

    # Download the files
    ftp_download_p(locations)
    
    # update the mappingfile with the file catalog
    addCatalogToMap(outfilepath=mappingfile, maptable=maptable, folderpath=filedir, catalog_label=catalog_label)
    
    # return to the home directory
    os.chdir(homedir)
    return(filedir)


def getDailyWRF_salathe2014(homedir, mappingfile, subdir='salathe2014/WWA_1950_2010/raw', catalog_label='dailywrf_salathe2014'):
    """
    Get the Salathe el al., 2014 raw Daily WRF files of interest using the reference mapping file
    
    homedir: (dir) the home directory to be used for establishing subdirectories
    mappingfile: (dir) the file path to the mappingfile, which contains the LAT, LONG_, and ELEV coordinates of interest
    subdir: (dir) the subdirectory to be established under homedir
    catalog_label: (str) the preferred name for the series of catalogged filepaths
    """
    # check and generate the Daily Meteorology raw WRF Salathe 2014 data directory
    filedir=os.path.join(homedir, subdir)
    ensure_dir(filedir)

    # read in the longitude and latitude points from the reference mapping file
    maptable = pd.read_csv(mappingfile)
    
    # compile the longitude and latitude points
    locations = compile_wrfnnrp_raw_Salathe2014_locations(maptable)

    # download the data
    wget_download_p(locations)
    
    # update the mappingfile with the file catalog
    addCatalogToMap(outfilepath=mappingfile, maptable=maptable, folderpath=filedir, catalog_label=catalog_label)
    
    # return to the home directory
    os.chdir(homedir)
    return(filedir)


def getDailyWRF_bcsalathe2014(homedir, mappingfile, subdir='salathe2014/WWA_1950_2010/bc', catalog_label='dailywrf_bcsalathe2014'):
    """
    Get the Salathe el al., 2014 bias corrected Daily WRF files of interest using the reference mapping file
    
    homedir: (dir) the home directory to be used for establishing subdirectories
    mappingfile: (dir) the file path to the mappingfile, which contains the LAT, LONG_, and ELEV coordinates of interest
    subdir: (dir) the subdirectory to be established under homedir
    catalog_label: (str) the preferred name for the series of catalogged filepaths
    """
    # check and generate the Daily Meteorology bias corrected WRF Salathe 2014 data directory
    filedir=os.path.join(homedir, subdir)
    ensure_dir(filedir)

    # read in the longitude and latitude points from the reference mapping file
    maptable = pd.read_csv(mappingfile)
    
    # compile the longitude and latitude points
    locations = compile_wrfnnrp_bc_Salathe2014_locations(maptable)

    # download the data
    wget_download_p(locations)
    
    # update the mappingfile with the file catalog
    addCatalogToMap(outfilepath=mappingfile, maptable=maptable, folderpath=filedir, catalog_label=catalog_label)
    
    # return to the home directory
    os.chdir(homedir)
    return(filedir)


# # Data Processing libraries


def filesWithPath(folderpath):
    """
    Create a list of filepaths for the files
    
    folderpath: (dir) the folder of interest
    """
    files =[os.path.join(folderpath, eachfile) for eachfile in os.listdir(folderpath) 
            if not eachfile.startswith('.') and not os.path.isdir(eachfile)] # exclude hidden files
    return(files)


def compareonvar(map_df, colvar='all'):
    """
    subsetting a dataframe based on some columns of interest
    
    map_df: (dataframe) the dataframe of the mappingfile table
    colvar: (str or list) the column(s) to use for subsetting; 'None' will return an outerjoin, 'all' will return an innerjoin
    """
    # apply row-wise inclusion based on a subset of columns
    if isinstance(colvar, type(None)):
        return(map_df)
    
    if colvar is 'all':
        # compare on all columns except the station info
        return(map_df.dropna())
    else:
        # compare on only the listed columns
        return(map_df.dropna(subset=colvar))


def mappingfileToDF(mappingfile, colvar='all'):
    """
    read in a dataframe and subset based on columns of interest
    
    mappingfile: (dir) the file path to the mappingfile, which contains the LAT, LONG_, and ELEV coordinates of interest
    colvar: (str or list) the column(s) to use for subsetting; 'None' will return an outerjoin, 'all' will return an innerjoin
    """
    # Read in the mappingfile as a data frame
    map_df = pd.read_csv(mappingfile)
    
    # select rows (datafiles) based on the colvar(s) chosen, default is 
    map_df = compareonvar(map_df=map_df, colvar=colvar)
    
    # compile summaries
    print(map_df.head())
          
    print('Number of gridded data files:'+ str(len(map_df)))
    print('Minimum elevation: ' + str(np.min(map_df.ELEV))+ 'm')
    print('Mean elevation: '+ str(np.mean(map_df.ELEV))+ 'm')
    print('Maximum elevation: '+ str(np.max(map_df.ELEV))+ 'm')
    
    return(map_df, len(map_df))


def read_in_all_files(map_df, dataset, metadata, file_start_date, file_end_date, file_time_step, file_colnames, file_delimiter, subset_start_date, subset_end_date):
    """
    Read in files based on dataset label
    
    map_df: (dataframe) the mappingfile clipped to the subset that will be read-in
    dataset: (str) the name of the dataset catalogged into map_df
    metadata (str) the dictionary that contains the metadata explanations; default is None
    file_colnames: (list) the list of shorthand variables; default is None
    file_start_date: (date) the start date of the files that will be read-in; default is None
    file_end_date: (date) the end date for the files that will be read in; default is None
    file_time_step: (str) the timedelta code that represents the difference between time points; default is 'D' (daily)    
    subset_start_date: (date) the start date of a date range of interest
    subset_end_date: (date) the end date of a date range of interest
    """
    # extract metadata if the information are not provided
    if pd.notnull(metadata):
        
        if isinstance(file_start_date, type(None)):
            file_start_date = metadata[dataset]['date_range']['start']
        
        if isinstance(file_end_date, type(None)):
            file_end_date = metadata[dataset]['date_range']['end']

        if isinstance(file_time_step, type(None)):
            file_time_step = metadata[dataset]['date_range']['time_step']

        if isinstance(file_colnames, type(None)):
            file_colnames = metadata[dataset]['variable_list']
        
        if isinstance(file_delimiter, type(None)):
            file_delimiter = metadata[dataset]['delimiter']
   
    #initialize dictionary and time sequence
    df_dict=dict()
    met_daily_dates=pd.date_range(file_start_date, file_end_date, freq=file_time_step) # daily
        
    # import data for all climate stations
    for ind, row in map_df.iterrows():
        tmp = pd.read_table(row[dataset], header=None, delimiter=file_delimiter, names=file_colnames)
        tmp.set_index(met_daily_dates, inplace=True)
        
        # subset to the date range of interest (default is file date range)
        tmp = tmp.iloc[(met_daily_dates>=subset_start_date) & (met_daily_dates<=subset_end_date),:]
        
        # set row indices
        df_dict[tuple(row[['FID','LAT','LONG_']].tolist())] = tmp
        
    return(df_dict)


def read_files_to_vardf(map_df, df_dict, gridclimname, dataset, metadata, 
                        file_start_date, file_end_date, file_delimiter, file_time_step, file_colnames, 
                        subset_start_date, subset_end_date, min_elev, max_elev):
    """
    # reads in the files to generate variables dataframes
    
    map_df: (dataframe) the mappingfile clipped to the subset that will be read-in
    df_dict: (dict) an existing dictionary where new computations will be stored
    gridclimname: (str) the suffix for the dataset to be named; if None is provided, default to the dataset name
    dataset: (str) the name of the dataset catalogged into map_df
    metadata: (str) the dictionary that contains the metadata explanations; default is None
    file_start_date: (date) the start date of the files that will be read-in; default is None
    file_end_date: (date) the end date for the files that will be read in; default is None
    file_delimiter: (str) a file parsing character to be used for file reading
    file_time_step: (str) the timedelta code that represents the difference between time points; default is 'D' (daily)    
    file_colnames: (list) the list of shorthand variables; default is None
    subset_start_date: (date) the start date of a date range of interest
    subset_end_date: (date) the end date of a date range of interest
    """
    # start time
    starttime = pd.datetime.now()
    
    # date range from ogh_meta file
    met_daily_dates=pd.date_range(file_start_date, file_end_date, freq=file_time_step)
    met_daily_subdates=pd.date_range(subset_start_date, subset_end_date, freq=file_time_step)
    
    # omit null entries or missing data file
    map_df = map_df.loc[pd.notnull(map_df[dataset]),:]
    print('Number of data files within elevation range ('+str(min_elev)+':'+str(max_elev)+'): '+str(len(map_df)))
    
    # iterate through each data file
    for eachvar in metadata[dataset]['variable_list']:

        # identify the variable column index
        usecols = [metadata[dataset]['variable_list'].index(eachvar)]
        
        # initiate df as a list
        df_list=[]
        
        # loop through each file
        for ind, row in map_df.iterrows():

            # consider rewriting the params to just select one column by index at a time
            var_series = dask.delayed(pd.read_table)(filepath_or_buffer=row[dataset],
                                                     delimiter=file_delimiter,header=None,usecols=usecols,
                                                     names=[tuple(row[['FID','LAT','LONG_']])])

            # append the series into the list of series
            df_list.append(var_series)

        # concatenate list of series (axis=1 is column-wise) into a dataframe
        df1 = dask.delayed(pd.concat)(df_list, axis=1)

        # set and subset date_range index
        df2 = df1.set_index(met_daily_dates, inplace=False).loc[met_daily_subdates]

        # end of variable table
        print(eachvar+ ' dataframe reading to start: ' + str(pd.datetime.now()-starttime))
        
        # assign dataframe to dictionary object
        df_dict['_'.join([eachvar, gridclimname])] = dask.compute(df2)[0]
        print(eachvar+ ' dataframe complete:' + str(pd.datetime.now()-starttime))

    return(df_dict)


def read_daily_streamflow(file_name, drainage_area_m2, file_colnames=None, delimiter='\t', header='infer'):
    # read in a daily streamflow data set    
    
    # if file_colnames are supplied, use header=None
    if file_colnames is not None:
        header=None
    
    # read in the data
    daily_data=pd.read_table(file_name, delimiter=delimiter, header=header) 
    
    # set columns, if header=None
    if file_colnames is not None:
        daily_data.columns=file_colnames
    else:
        file_colnames=list(daily_data.columns)
        
    # calculate cfs to cms conversion, or vice versa
    if 'flow_cfs' in daily_data.columns:
        flow_cfs=daily_data['flow_cfs']
        flow_cms=flow_cfs/(3.28084**3)
        flow_mmday=flow_cms*1000*3600*24/drainage_area_m2 
    elif 'flow_cms' in daily_data.columns:
        flow_cms=daily_data['flow_cms']
        flow_cfs=flow_cms*(3.28084**3)
        flow_mmday=flow_cms*1000*3600*24/drainage_area_m2
            
    # determine the datetime
    date_index=[file_colnames.index(each) for each in ['year','month','day']]
    row_dates=pd.to_datetime(daily_data[date_index])
    
    # generate the daily_flow and set the datetime as row indices
    daily_flow=pd.concat([flow_cfs, flow_cms, flow_mmday],axis=1)
    daily_flow.set_index(row_dates, inplace=True)
    daily_flow.columns=['flow_cfs', 'flow_cms', 'flow_mmday']
    return(daily_flow)


def read_daily_precip(file_name, file_colnames=None, header='infer', delimiter='\s+'):
    # read in a daily precipitation data set
    
    # if file_colnames are supplied, use header=None
    if ps.notnull(file_colnames):
        header=None
    
    # read in the data
    daily_data=pd.read_table(file_name, delimiter=delimiter, header=header) 
    
    # set columns, if header=None
    if pd.notnull(file_colnames):
        daily_data.columns=file_colnames
    else:
        file_colnames=list(daily_data.columns)
    
    # calculate cfs to cms conversion, or vice versa
    if 'precip_m' in daily_data.columns:
        precip_m=daily_data['precip_m']
        precip_mm=precip_m*1000
    
    # determine the datetime
    date_index=[file_colnames.index(each) for each in ['year','month','day']]
    row_dates=pd.to_datetime(daily_data[date_index])
    
    # generate the daily_flow and set the datetime as row indices
    daily_precip=pd.concat([precip_m, precip_mm],axis=1)
    daily_precip.set_index(row_dates, inplace=True)
    daily_precip.columns=['precip_m', 'precip_mm']
    return(daily_precip)


def read_daily_snotel(file_name, file_colnames=None, usecols=None, delimiter=',', header='infer'):
    # read in a daily SNOTEL observation data set
    
    # if file_colnames are supplied, use header=None
    if file_colnames is not None:
        header=None
    
    # read in the data
    daily_data=pd.read_table(file_name, usecols=usecols, header=header, delimiter=delimiter)
    
    # reset the colnames
    daily_data.columns=['Date', 'Tmax_C', 'Tmin_C', 'Tavg_C', 'Precip_mm']
    
    # transform the data
    daily_data['Tmax_C']=(daily_data['Tmax_C'] -32)/1.8
    daily_data['Tmin_C']=(daily_data['Tmin_C'] -32)/1.8
    daily_data['Tavg_C']=(daily_data['Tavg_C'] -32)/1.8
    daily_data['Precip_mm']=daily_data['Precip_mm'] *25.4
    
    # determine the datetime
    row_dates=pd.to_datetime(daily_data.Date)
    
    # generate the daily_flow and set the datetime as row indices
    daily_snotel=daily_data[['Tmax_C', 'Tmin_C', 'Tavg_C', 'Precip_mm']]
    daily_snotel.set_index(row_dates, inplace=True)
    return(daily_snotel)


def read_daily_coop(file_name, file_colnames=None, usecols=None, delimiter=',', header='infer'):
    # read in a daily COOP observation data set
    
    # if file_colnames are supplied, use header=None
    if file_colnames is not None:
        header=None
    
    # read in the data
    daily_data=pd.read_table(file_name, usecols=usecols, header=header, delimiter=delimiter, 
                             date_parser=lambda x: pd.datetime.strptime(x, '%Y%m%d'), 
                             parse_dates=[0], 
                             na_values=-9999)
    
    # reset the colnames
    daily_data.columns=['Date', 'Precip_mm','Tmax_C', 'Tmin_C', 'Tavg_C']
    
    # transform the data
    daily_data['Tmax_C']=(daily_data['Tmax_C'] -32)/1.8
    daily_data['Tmin_C']=(daily_data['Tmin_C'] -32)/1.8
    daily_data['Tavg_C']=(daily_data['Tavg_C'] -32)/1.8
    daily_data['Precip_mm']=daily_data['Precip_mm'] *25.4
    
    # determine the datetime
    row_dates=pd.to_datetime(daily_data.Date)
    
    # generate the daily_flow and set the datetime as row indices
    daily_coop=daily_data[['Precip_mm','Tmax_C', 'Tmin_C', 'Tavg_C']]
    daily_coop.set_index(row_dates, inplace=True)
    return(daily_coop)

# ### Data Processing functions

def generateVarTables(file_dict, gridclimname, dataset, metadata, df_dict=None):
    """
    Slice the files by their common variable
    
    all_files: (dict) a dictionary of dataframes for each tabular datafile
    dataset: (str) the name of the dataset
    metadata (dict) the dictionary that contains the metadata explanations; default is None
    """
    # combine the files into a pandas panel
    panel = pd.Panel.from_dict(file_dict)

    # initiate output dictionary
    if pd.isnull(df_dict):
        df_dict = dict()
    
    # slice the panel for each variable in list
    for eachvar in metadata[dataset]['variable_list']:
        df_dict['_'.join([eachvar, gridclimname])] = panel.xs(key=eachvar, axis=2)
        
    return(df_dict)


# compare two date sets for the start and end of the overlapping dates
def overlappingDates(date_set1, date_set2):
    # find recent date
    if date_set1[0] > date_set2[0]:
        start_date = date_set1[0]
    else:
        start_date = date_set2[0]
    
    # find older date
    if date_set1[-1] < date_set2[-1]:
        end_date = date_set1[-1]
    else:
        end_date = date_set2[-1]
    return(start_date, end_date)


# Calculate means by 8 different methods
def multigroupMeans(VarTable, n_stations, start_date, end_date):
    Var_daily = VarTable.loc[start_date:end_date, range(0,n_stations)]
    
    # e.g., Mean monthly temperature at each station
    month_daily=Var_daily.groupby(Var_daily.index.month).mean() # average monthly minimum temperature at each station
    
    # e.g., Mean monthly temperature averaged for all stations in analysis
    meanmonth_daily=month_daily.mean(axis=1)
    
    # e.g., Mean monthly temperature for minimum and maximum elevation stations
    meanmonth_min_maxelev_daily=Var_daily.loc[:,analysis_elev_max_station].groupby(Var_daily.index.month).mean()
    meanmonth_min_minelev_daily=Var_daily.loc[:,analysis_elev_min_station].groupby(Var_daily.index.month).mean()
    
    # e.g., Mean annual temperature
    year_daily=Var_daily.groupby(Var_daily.index.year).mean()
    
    # e.g., mean annual temperature each year for all stations
    meanyear_daily=year_daily.mean(axis=1)
    
    # e.g., mean annual min temperature for all years, for all stations
    meanallyear_daily=np.nanmean(meanyear_daily)
    
    # e.g., anomoly per year compared to average
    anom_year_daily=meanyear_daily-meanallyear_daily
    
    return(month_daily, 
           meanmonth_daily, 
           meanmonth_min_maxelev_daily, 
           meanmonth_min_minelev_daily, 
           year_daily, 
           meanyear_daily, 
           meanallyear_daily,
           anom_year_daily)


def specialTavgMeans(VarTable):
    Var_daily = VarTable.loc[start_date:end_date, range(0,n_stations)]
    
    # Average temperature for each month at each station
    permonth_daily=Var_daily.groupby(pd.TimeGrouper("M")).mean()
    
    # Average temperature each month averaged at all stations
    meanpermonth_daily=permonth_daily.mean(axis=1)
    
    # Average monthly temperature for all stations
    meanallpermonth_daily=meanpermonth_daily.mean(axis=0)
    
    # anomoly per year compared to average
    anom_month_daily=(meanpermonth_daily-meanallpermonth_daily)/1000
    
    return(permonth_daily,
          meanpermonth_daily,
          meanallpermonth_daily,
          anom_month_daily)


def aggregate_space_time_average(VarTable, df_dict, suffix, start_date, end_date):
    """
    VarTable: (dataframe) a dataframe with date ranges as the index
    df_dict: (dict) a dictionary to which computed outputs will be stored
    suffix: (str) a string representing the name of the original table
    start_date: (date) the start of the date range within the original table
    end_date: (date) the end of the date range within the original table
    """
    starttime = pd.datetime.now()
    
    # subset dataframe to the date range of interest
    Var_daily = VarTable.loc[start_date:end_date,:]
    
    # Mean monthly temperature at each station
    df_dict['month_'+suffix] = Var_daily.groupby(Var_daily.index.month).mean() 
    
    # Mean monthly temperature averaged for all stations in analysis
    df_dict['meanmonth_'+suffix] = Var_daily.groupby(Var_daily.index.month).mean().mean(axis=1)

    # Mean annual temperature
    df_dict['year_'+suffix] = Var_daily.groupby(Var_daily.index.year).mean()
    
    # mean annual temperature each year for all stations
    df_dict['meanyear_'+suffix] = Var_daily.groupby(Var_daily.index.year).mean().mean(axis=1)
    
    # mean annual temperature for all years, for all stations
    df_dict['meanallyear_'+suffix] = Var_daily.mean(axis=1).mean(axis=0)
    
    # anomaly per year compared to average
    df_dict['anom_year_'+suffix] = df_dict['meanyear_'+suffix] - df_dict['meanallyear_'+suffix]
    
    print(suffix+ ' calculations completed in ' + str(pd.datetime.now()-starttime))
    return(df_dict)


def aggregate_space_time_sum(VarTable, n_stations, start_date, end_date):
    Var_daily = VarTable.loc[start_date:end_date, range(0,n_stations)]
    
    # Average precipitation per month at each station
    permonth_daily=Var_daily.groupby(pd.TimeGrouper("M")).sum()
    
    # Average precipitation per month averaged at all stations
    meanpermonth_daily=permonth_daily.mean(axis=1)
    
    # Average monthly precipitation averaged at all stations
    meanmonth_daily= meanpermonth_daily.groupby(meanpermonth_daily.index.month).mean()
    
    return(Var_daily,
           permonth_daily,
           meanpermonth_daily,
           meanmonth_daily)


#def aggregate_space_time_sum(VarTable, n_stations, start_date, end_date):
#    Var_daily = VarTable.loc[start_date:end_date, range(0,n_stations)]
#    
#    # Average precipitation per month at each station
#    permonth_daily=Var_daily.groupby(pd.TimeGrouper("M")).sum()
#    
#    # Average precipitation per month averaged at all stations
#    meanpermonth_daily=permonth_daily.mean(axis=1)
#    
#    # Average monthly precipitation averaged at all stations
#    meanmonth_daily= meanpermonth_daily.groupby(meanpermonth_daily.index.month).mean()
#    
#    return(Var_daily,
#          permonth_daily,
#          meanpermonth_daily,
#          meanmonth_daily)


#def specialTavgMeans(VarTable):
#    Var_daily = VarTable.loc[start_date:end_date, range(0,n_stations)]
#    
#    # Average temperature for each month at each station
#    permonth_daily=Var_daily.groupby(pd.TimeGrouper("M")).mean()
#    
#    # Average temperature each month averaged at all stations
#    meanpermonth_daily=permonth_daily.mean(axis=1)
#    
#    # Average monthly temperature for all stations
#    meanallpermonth_daily=meanpermonth_daily.mean(axis=0)
#    
#    # anomoly per year compared to average
#    anom_month_daily=(meanpermonth_daily-meanallpermonth_daily)/1000
#    
#    return(permonth_daily,
#          meanpermonth_daily,
#          meanallpermonth_daily,
#          anom_month_daily)


def plotTavg(dictionary, loc_name, start_date, end_date):
    # Plot 1: Monthly temperature analysis of Livneh data
    if 'meanmonth_temp_avg_liv2013_met_daily' and 'meanmonth_temp_avg_wrf2014_met_daily' not in dictionary.keys():
        pass

    # generate month indices
    wy_index=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    wy_numbers=[10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    month_strings=[ 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept']
    
    # initiate the plot object
    fig, ax=plt.subplots(1,1,figsize=(10, 6))

    if 'meanmonth_temp_avg_liv2013_met_daily' in dictionary.keys():
        # Liv2013
        plt.plot(wy_index, dictionary['meanmonth_maxelev_temp_avg_liv2013_met_daily'][wy_numbers],'r*--',linewidth=1, label='Liv Tavg- Max Elev='+str(dictionary['analysis_elev_max_cutoff'])+"-"+str(dictionary['analysis_elev_max'])+'m')
        
        plt.plot(wy_index, dictionary['meanmonth_midelev_temp_avg_liv2013_met_daily'][wy_numbers],'r-', linewidth=1, label='Liv Tavg- Mid Elev='+str(dictionary['analysis_elev_min_cutoff'])+"-"+str(dictionary['analysis_elev_max_cutoff'])+'m')
        
        plt.plot(wy_index, dictionary['meanmonth_minelev_temp_avg_liv2013_met_daily'][wy_numbers],'rX--',linewidth=1, label='Liv Tavg- Min Elev='+str(dictionary['analysis_elev_min'])+"-"+str(dictionary['analysis_elev_min_cutoff'])+'m')
    
    
    if 'meanmonth_temp_avg_wrf2014_met_daily' in dictionary.keys():
        # WRF2014
        plt.plot(wy_index, dictionary['meanmonth_maxelev_temp_avg_wrf2014_met_daily'][wy_numbers],'b^--',linewidth=1, label='WRF Tavg- Max Elev='+str(dictionary['analysis_elev_max_cutoff'])+"-"+str(dictionary['analysis_elev_max'])+'m')
        
        plt.plot(wy_index, dictionary['meanmonth_midelev_temp_avg_wrf2014_met_daily'][wy_numbers],'b-',linewidth=1, label='WRF Tavg- Mid Elev='+str(dictionary['analysis_elev_min_cutoff'])+"-"+str(dictionary['analysis_elev_max_cutoff'])+'m')
        
        plt.plot(wy_index, dictionary['meanmonth_minelev_temp_avg_wrf2014_met_daily'][wy_numbers],'bo--',linewidth=1, label='WRF Tavg- Min Elev='+str(dictionary['analysis_elev_min'])+"-"+str(dictionary['analysis_elev_min_cutoff'])+'m')

    if 'meanmonth_temp_avg_livneh2013_wrf2014bc_met_daily' in dictionary.keys():
        # WRF2014
        plt.plot(wy_index, dictionary['meanmonth_maxelev_temp_avg_livneh2013_wrf2014bc_met_daily'][wy_numbers],'g^--',linewidth=1, label='WRFbc Tavg- Max Elev='+str(dictionary['analysis_elev_max_cutoff'])+"-"+str(dictionary['analysis_elev_max'])+'m')
        
        plt.plot(wy_index, dictionary['meanmonth_midelev_temp_avg_livneh2013_wrf2014bc_met_daily'][wy_numbers],'g-',linewidth=1, label='WRFbc Tavg- Mid Elev='+str(dictionary['analysis_elev_min_cutoff'])+"-"+str(dictionary['analysis_elev_max_cutoff'])+'m')
        
        plt.plot(wy_index, dictionary['meanmonth_minelev_temp_avg_livneh2013_wrf2014bc_met_daily'][wy_numbers],'go--',linewidth=1, label='WRFbc Tavg- Min Elev='+str(dictionary['analysis_elev_min'])+"-"+str(dictionary['analysis_elev_min_cutoff'])+'m')

        
    # add reference line at y=0
    plt.plot([1, 12],[0, 0], 'k-',linewidth=1)

    plt.ylabel('Temperature (deg C)',fontsize=14)
    plt.xlabel('Month',fontsize=14)
    plt.xlim(1,12);
    plt.xticks(wy_index, month_strings);
        
    plt.tick_params(labelsize=12)
    plt.legend(loc='best')
    plt.grid(which='both')
    plt.title(str(loc_name)+'\nAverage Temperature\n Years: '+str(start_date.year)+'-'+str(end_date.year)+'; Elevation: '+str(dictionary['analysis_elev_min'])+'-'+str(dictionary['analysis_elev_max'])+'m', fontsize=16)
    
    plt.savefig('avg_monthly_temp'+str(loc_name)+'.png')
    plt.show()
    
def plotPavg(dictionary, loc_name, start_date, end_date):
    # Plot 1: Monthly temperature analysis of Livneh data
    if 'meanmonth_precip_liv2013_met_daily' and 'meanmonth_precip_wrf2014_met_daily' not in dictionary.keys():
        pass

    # generate month indices
    wy_index=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    wy_numbers=[10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    month_strings=[ 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept']
    
    # initiate the plot object
    fig, ax=plt.subplots(1,1,figsize=(10, 6))

    if 'meanmonth_precip_liv2013_met_daily' in dictionary.keys():
        # Liv2013

        plt.plot(wy_index, dictionary['meanmonth_maxelev_precip_liv2013_met_daily'][wy_numbers],'r^--',linewidth=1, label='Liv Precip- Max Elev='+str(dictionary['analysis_elev_max_cutoff'])+"-"+str(dictionary['analysis_elev_max'])+'m')
        plt.plot(wy_index, dictionary['meanmonth_midelev_precip_liv2013_met_daily'][wy_numbers],'r-', linewidth=1, label='Liv Precip- Mid Elev='+str(dictionary['analysis_elev_min_cutoff'])+"-"+str(dictionary['analysis_elev_max_cutoff'])+'m')  
        plt.plot(wy_index, dictionary['meanmonth_minelev_precip_liv2013_met_daily'][wy_numbers],'ro--',linewidth=1, label='Liv Precip- Min Elev='+str(dictionary['analysis_elev_min'])+"-"+str(dictionary['analysis_elev_min_cutoff'])+'m')
    
    if 'meanmonth_temp_avg_wrf2014_met_daily' in dictionary.keys():
        # WRF2014

        plt.plot(wy_index, dictionary['meanmonth_maxelev_precip_wrf2014_met_daily'][wy_numbers],'b^--',linewidth=1, label='WRF Precip- Max Elev='+str(dictionary['analysis_elev_max_cutoff'])+"-"+str(dictionary['analysis_elev_max'])+'m')
        plt.plot(wy_index, dictionary['meanmonth_midelev_precip_wrf2014_met_daily'][wy_numbers],'b-',linewidth=1, label='WRF Precip- Mid Elev='+str(dictionary['analysis_elev_min_cutoff'])+"-"+str(dictionary['analysis_elev_max_cutoff'])+'m')
        plt.plot(wy_index, dictionary['meanmonth_minelev_precip_wrf2014_met_daily'][wy_numbers],'bo--',linewidth=1, label='WRF Precip- Min Elev='+str(dictionary['analysis_elev_min'])+"-"+str(dictionary['analysis_elev_min_cutoff'])+'m')

    if 'meanmonth_temp_avg_livneh2013_wrf2014bc_met_daily' in dictionary.keys():
        # WRF2014
        plt.plot(wy_index, dictionary['meanmonth_maxelev_precip_livneh2013_wrf2014bc_met_daily'][wy_numbers],'g^--',linewidth=1, label='WRFbc Precip- Max Elev='+str(dictionary['analysis_elev_max_cutoff'])+"-"+str(dictionary['analysis_elev_max'])+'m')
        
        plt.plot(wy_index, dictionary['meanmonth_midelev_precip_livneh2013_wrf2014bc_met_daily'][wy_numbers],'g-',linewidth=1, label='WRFbc Precip- Mid Elev='+str(dictionary['analysis_elev_min_cutoff'])+"-"+str(dictionary['analysis_elev_max_cutoff'])+'m')
        
        plt.plot(wy_index, dictionary['meanmonth_minelev_precip_livneh2013_wrf2014bc_met_daily'][wy_numbers],'go--',linewidth=1, label='WRFbc Precip- Min Elev='+str(dictionary['analysis_elev_min'])+"-"+str(dictionary['analysis_elev_min_cutoff'])+'m')

    # add reference line at y=0
    plt.plot([1, 12],[0, 0], 'k-',linewidth=1)

    plt.ylabel('Precip (mm)',fontsize=14)
    plt.xlabel('Month',fontsize=14)
    plt.xlim(1,12);
    plt.xticks(wy_index, month_strings);
        
    plt.tick_params(labelsize=12)
    plt.legend(loc='best')
    plt.grid(which='both')
    plt.title(str(loc_name)+'\nAverage Precipitation\n Years: '+str(start_date.year)+'-'+str(end_date.year)+'; Elevation: '+str(dictionary['analysis_elev_min'])+'-'+str(dictionary['analysis_elev_max'])+'m', fontsize=16)
    plt.savefig('avg_monthly_precip'+str(loc_name)+'.png')
    plt.show()
    

def gridclim_dict(mappingfile, dataset, gridclimname=None, metadata=None, min_elev=None, max_elev=None,
                  file_start_date=None, file_end_date=None, file_time_step=None,
                  file_colnames=None, file_delimiter=None,
                  subset_start_date=None, subset_end_date=None, df_dict=None):
    """
    # pipelined operation for assimilating data, processing it, and standardizing the plotting
    
    mappingfile: (dir) the path directory to the mappingfile
    dataset: (str) the name of the dataset within mappingfile to use
    gridclimname: (str) the suffix for the dataset to be named; if None is provided, default to the dataset name
    metadata: (str) the dictionary that contains the metadata explanations; default is None
    min_elev: (float) the minimum elevation criteria; default is None
    max_elev: (float) the maximum elevation criteria; default is None
    file_start_date: (date) the start date of the files that will be read-in; default is None
    file_end_date: (date) the end date for the files that will be read in; default is None
    file_time_step: (str) the timedelta code that represents the difference between time points; default is 'D' (daily)    
    file_colnames: (list) the list of shorthand variables; default is None
    file_delimiter: (str) a file parsing character to be used for file reading
    subset_start_date: (date) the start date of a date range of interest
    subset_end_date: (date) the end date of a date range of interest
    df_dict: (dict) an existing dictionary where new computations will be stored
    """
    
    # generate the climate locations and n_stations
    locations_df, n_stations = mappingfileToDF(mappingfile, colvar='all')
    
    # generate the climate station info
    if pd.isnull(min_elev):
        min_elev = locations_df.ELEV.min()
    
    if pd.isnull(max_elev):
        max_elev = locations_df.ELEV.max()
    
    # extract metadata if the information are not provided
    if not isinstance(metadata, type(None)):
        
        if isinstance(file_start_date, type(None)):
            file_start_date = metadata[dataset]['date_range']['start']
        
        if isinstance(file_end_date, type(None)):
            file_end_date = metadata[dataset]['date_range']['end']

        if isinstance(file_time_step, type(None)):
            file_time_step = metadata[dataset]['date_range']['time_step']

        if isinstance(file_colnames, type(None)):
            file_colnames = metadata[dataset]['variable_list']
        
        if isinstance(file_delimiter, type(None)):
            file_delimiter = metadata[dataset]['delimiter']
        
    # take all defaults if subset references are null
    if pd.isnull(subset_start_date):
        subset_start_date = file_start_date
    
    if pd.isnull(subset_end_date):
        subset_end_date = file_end_date
        
    # initiate output dictionary df_dict was null
    if pd.isnull(df_dict):
        df_dict = dict()
        
    if pd.isnull(gridclimname):
        if pd.notnull(dataset):
            gridclimname=dataset
        else:
            print('no suffix name provided. Provide a gridclimname or dataset label.')
            return
    
    # assemble the stations within min and max elevantion ranges
    locations_df = locations_df[(locations_df.ELEV >= min_elev) & (locations_df.ELEV <= max_elev)]
    
    # create dictionary of dataframe
    df_dict = read_files_to_vardf(map_df=locations_df,
                                  dataset=dataset, 
                                  metadata=metadata, 
                                  gridclimname=gridclimname,
                                  file_start_date=file_start_date, 
                                  file_end_date=file_end_date, 
                                  file_delimiter=file_delimiter, 
                                  file_time_step=file_time_step, 
                                  file_colnames=file_colnames, 
                                  subset_start_date=subset_start_date, 
                                  subset_end_date=subset_end_date,
                                  min_elev=min_elev, 
                                  max_elev=max_elev,
                                  df_dict=df_dict)
    # 
    vardf_list = [eachvardf for eachvardf in df_dict.keys() if eachvardf.endswith(gridclimname)]
    # loop through the dictionary to compute each aggregate_space_time_average object
    for eachvardf in vardf_list:
            
        # update the dictionary with spatial and temporal average computations
        df_dict.update(aggregate_space_time_average(VarTable=df_dict[eachvardf],
                                                    df_dict=df_dict,
                                                    suffix=eachvardf, 
                                                    start_date=subset_start_date, 
                                                    end_date=subset_end_date))
            
        # if the number of stations exceeds 500, remove daily time-series dataframe
        if len(locations_df)>500:
            del df_dict[eachvardf]
                
    return(df_dict)


def compute_diffs(df_dict, df_str, gridclimname1, gridclimname2, prefix1, prefix2='meanmonth', comp_dict=None):
    #Compute difference between monthly means for some data (e.g,. Temp) for two different gridded datasets (e.g., Liv, WRF)
    
    if isinstance(comp_dict, type(None)):
        comp_dict=dict()
        
    for each1 in prefix1:
        for each2 in prefix2:
            comp_dict['_'.join([str(each1),df_str])] = df_dict['_'.join([each2,each1,gridclimname1])]-df_dict['_'.join([each2,each1,gridclimname2])]
    return(comp_dict)


def compute_ratios(df_dict, df_str, gridclimname1, gridclimname2, prefix1, prefix2='meanmonth', comp_dict=None):
    #Compute difference between monthly means for some data (e.g,. Temp) for two different gridded datasets (e.g., Liv, WRF)
    
    if isinstance(comp_dict, type(None)):
        comp_dict=dict()
    
    for each1 in prefix1:
        for each2 in prefix2:
            comp_dict['_'.join([str(each1),df_str])] = df_dict['_'.join([each2,each1,gridclimname1])]/df_dict['_'.join([each2,each1,gridclimname2])]
    return(comp_dict)


def compute_elev_diffs(df_dict, df_str, gridclimname1, prefix1, prefix2a='meanmonth_minelev_', prefix2b='meanmonth_maxelev_'):
    comp_dict=dict()
    for each1 in prefix1:
        comp_dict[str(each1)+df_str] = df_dict[prefix2a+each1+gridclimname1]-df_dict[prefix2b+each1+gridclimname1]
    return(comp_dict)


def monthlyBiasCorrection_deltaTratioP_Livneh_METinput(homedir, mappingfile, BiasCorr,
                                                 lowrange='0to1000m', LowElev=range(0,1000),
                                                 midrange='1000to1500m', MidElev=range(1001,1501),
                                                 highrange='1500to3000m', HighElev=range(1501,3000),
                                                 data_dir=None, file_start_date=None, file_end_date=None):

    np.set_printoptions(precision=3)
    
    # take liv2013 date set date range as default if file reference dates are not given
    if isinstance(file_start_date, type(None)):
        file_start_date = datetime(1915,1,1)
        
    if isinstance(file_end_date, type(None)):
        file_end_date = datetime(2011,12,31)
    
    # generate the month vector
    month = pd.date_range(start=file_start_date, end=file_end_date).month
    month = pd.DataFrame({'month':month})
    
    # create NEW directory
    dest_dir = os.path.join(homedir, 'biascorrWRF_liv')
    if not os.path.exists(dest_dir):
        os.mkdir(dest_dir)
        print('destdir created')
    
    # read in the Elevation table
    zdiff = pd.read_table(mappingfile, sep=',', header='infer')
    zdiff = zdiff.rename(columns={'RASTERVALU':'Elev','ELEV':'Elev'})
    zdiff = zdiff[['LAT','LONG_', 'Elev']]
    zdiff['filename'] = zdiff[['LAT','LONG_']].apply(lambda x: '_'.join(['Meteorology_Livneh_CONUSExt_v.1.2_2013',str(x[0]), str(x[1])]), axis=1)
    #print(zdiff[0:10])

    # lapse rate vector by month
    # temperature adjustment vector by month

    # identify the files to read
    print('reading in data_long_lat files')
    data_files = [os.path.join(data_dir,dat) for dat in os.listdir(data_dir) if os.path.basename(dat).startswith('Meteorology_Livneh_CONUSExt_v.1.2_2013')]
    print('done reading data_long_lat files')
    
    # loop through each file
    for eachfile in data_files:
        
        # subset the zdiff table using the eachfile's filename, then assign Elevation to equal the Elev value
        Elevation = zdiff[zdiff['filename']==os.path.basename(eachfile)]['Elev'].reset_index(drop=True)
        print(Elevation)
                
        # decide on the elevation-based Tcorr
        #print('Convert BiasCorr to a df')
        if Elevation.iloc[0] in LowElev:
            BiasCorr_sub = {k: v for k, v in BiasCorr.items() if k.endswith('_'+lowrange)}
            #BiasCorr_sub_df = pd.DataFrame.from_dict(BiasCorr_sub, orient='columns').reset_index()
            #BiasCorr_sub_df.columns = ['month', 'precip', 'Tmax', 'Tmin']
            
        elif Elevation.iloc[0] in MidElev:
            BiasCorr_sub = {k: v for k, v in BiasCorr.items() if k.endswith('_'+midrange)}
            #BiasCorr_sub_df = pd.DataFrame.from_dict(BiasCorr_sub, orient='columns').reset_index()
            #BiasCorr_sub_df.columns = ['month', 'precip', 'Tmax', 'Tmin']
        
        elif Elevation.iloc[0] in HighElev:
            BiasCorr_sub = {k: v for k, v in BiasCorr.items() if k.endswith('_'+highrange)}
            #BiasCorr_sub_df = pd.DataFrame.from_dict(BiasCorr_sub, orient='columns').reset_index()
            #BiasCorr_sub_df.columns = ['month', 'precip', 'Tmax', 'Tmin']
       
        #print('reading in eachfile')
        read_dat = pd.read_table(eachfile, delimiter='\s+', header=None)
        read_dat.columns = ['precip', 'temp_max','temp_min','wind']
        # print('done reading eachfile')

        # extrapolate monthly values for each variable
        for eachvar in ['precip', 'temp_max', 'temp_min']:
            BiasCorr_sub_df = [BiasCorr_sub[eachkey] for eachkey in BiasCorr_sub.keys() if eachkey.startswith(eachvar)]
            
            # subset the column for the eachfile station number
            BiasCorr_sub_df = BiasCorr_sub_df.loc[:,zdiff[zdiff['filename']==eachfile].index]
            BiasCorr_sub_df.columns = ['var']
            
            # regenerate the month
            BiasCorr_sub_df = BiasCorr_sub_df.reset_index().rename(columns={'index':'month'})
            
            # generate s-vectors
            month_obj = month.merge(BiasCorr_sub_df, how='left', on='month')
            
            # generate the s-vector
            s = pd.Series(month_obj.var)
            
            #
            if eachvar=='precip':
                read_dat[eachvar] = np.array(read_dat[eachvar])*np.array(s)
            else:
                read_dat[eachvar] = np.array(read_dat[eachvar])+np.array(s)    
        
        #print('grabbing the S vector of monthlapse after the merge between month and Tcorr_df')
        #print('number of corrections to apply: '+str(len(month)))
        
        # write it out to the new destination location
        read_dat.to_csv(os.path.join(dest_dir, os.path.basename(eachfile)), sep='\t', header=None, index=False)
        print(os.path.join(dest_dir, os.path.basename(eachfile)))
    
    print('mission complete.')
    print('this device will now self-destruct.')
    print('just kidding.')


def monthlyBiasCorrection_WRFlongtermmean_elevationbins_METinput(homedir, mappingfile, BiasCorr,
                                                 lowrange='0to1000m', LowElev=range(0,1000),
                                                 midrange='1000to1500m', MidElev=range(1001,1501),
                                                 highrange='1500to3000m', HighElev=range(1501,3000),
                                                 data_dir=None,
                                                 file_start_date=None,
                                                 file_end_date=None):

    np.set_printoptions(precision=3)
    
    # take liv2013 date set date range as default if file reference dates are not given
    if isinstance(file_start_date, type(None)):
        file_start_date = datetime(1950,1,1)
        
    if isinstance(file_end_date, type(None)):
        file_end_date = datetime(2010,12,31)
    
    # generate the month vector
    month = pd.date_range(start=file_start_date, end=file_end_date).month
    month = pd.DataFrame({'month':month})
    
    # create NEW directory
    dest_dir = os.path.join(homedir, 'biascorr_WRF_ltm')
    if not os.path.exists(dest_dir):
        os.mkdir(dest_dir)
        print('destdir created')
    
    # read in the Elevation table
    zdiff = pd.read_table(mappingfile, sep=',', header='infer')
    zdiff = zdiff.rename(columns={'RASTERVALU':'Elev','ELEV':'Elev'})
    zdiff = zdiff[['LAT','LONG_', 'Elev']]
    zdiff['filename'] = zdiff[['LAT','LONG_']].apply(lambda x: '_'.join(['data',str(x[0]), str(x[1])]), axis=1)
    #print(zdiff[0:10])

    # lapse rate vector by month
    # temperature adjustment vector by month

    # identify the files to read
    print('reading in data_long_lat files')
    data_files = [os.path.join(data_dir,dat) for dat in os.listdir(data_dir) if os.path.basename(dat).startswith('data')]
    #print('done reading data_long_lat files')
    
    # loop through each file
    for eachfile in data_files:
        
        # subset the zdiff table using the eachfile's filename, then assign Elevation to equal the Elev value
        Elevation = zdiff[zdiff['filename']==os.path.basename(eachfile)]['Elev'].reset_index(drop=True)
        print(Elevation)
                
        # decide on the elevation-based Tcorr
        #print('Convert BiasCorr to a df')
        if Elevation.iloc[0] in LowElev:
            BiasCorr_sub = {k: v for k, v in BiasCorr.items() if k.endswith('_'+lowrange)}
            BiasCorr_sub_df = pd.DataFrame.from_dict(BiasCorr_sub, orient='columns').reset_index()
            BiasCorr_sub_df.columns = ['month', 'precip', 'Tmax', 'Tmin']
            
        elif Elevation.iloc[0] in MidElev:
            BiasCorr_sub = {k: v for k, v in BiasCorr.items() if k.endswith('_'+midrange)}
            BiasCorr_sub_df = pd.DataFrame.from_dict(BiasCorr_sub, orient='columns').reset_index()
            BiasCorr_sub_df.columns = ['month', 'precip', 'Tmax', 'Tmin']
        
        elif Elevation.iloc[0] in HighElev:
            BiasCorr_sub = {k: v for k, v in BiasCorr.items() if k.endswith('_'+highrange)}
            BiasCorr_sub_df = pd.DataFrame.from_dict(BiasCorr_sub, orient='columns').reset_index()
            BiasCorr_sub_df.columns = ['month', 'precip', 'Tmax', 'Tmin']
       
        print('reading in eachfile')
        read_dat = pd.read_table(eachfile, delimiter='\s+', header=None)
        read_dat.columns = ['precip', 'Tmax','Tmin','wind']
        print('done reading eachfile')

        # extrapolate monthly values
        month_obj = month.merge(BiasCorr_sub_df, how='left', on='month')
        #print('merged month with Tcorr_df')
        #print(month_obj.head(35))
        
        # generate s-vectors
        s1 = pd.Series(month_obj.Tmin)
        s2 = pd.Series(month_obj.Tmax)
        s3 = pd.Series(month_obj.precip)
        
        read_dat['Tmin'] = np.array(read_dat.Tmin)+np.array(s1)
        read_dat['Tmax'] = np.array(read_dat.Tmax)+np.array(s2)
        read_dat['precip'] = np.array(read_dat.precip)*np.array(s3)
        
        # write it out to the new destination location
        read_dat.to_csv(os.path.join(dest_dir, os.path.basename(eachfile)), sep='\t', header=None, index=False)
        print(os.path.join(dest_dir, os.path.basename(eachfile)))
    
    print('mission complete.')
    print('this device will now self-destruct.')
    print('just kidding.')


def switchUpVICSoil(input_file=None,
                    output_file='soil',
                    mappingfile=None,
                    homedir=None):
    #Read in table of VIC soil inputs -- assumes all Lat/Long set to zero
    soil_base = pd.read_table(input_file,header=None)

    #Make a list of all lat/long values
    latlong=soil_base.apply(lambda x:tuple([x[2],x[3]]), axis=1)

    #Read in mappingfile from TreatGeoSelf()
    maptable = pd.read_table(mappingfile,sep=",")

    #Make a list Lat/Long files that need to switched up 
    latlong_1=maptable.apply(lambda x:tuple([x['LAT'],x['LONG_']]), axis=1)

    #Switch up from 0 to 1 so VIC will run for this Lat/Long point - print new output file (VIC model input file)
    soil_base[0] = latlong.apply(lambda x: 1 if x in set(latlong_1) else 0)        
    soil_base.to_csv(output_file, header=False, index=False, sep="\t")
    print(str(soil_base[0].sum()) +' VIC grid cells have successfully been switched up.') 
    print('Check your home directory for your new VIC soil model input set to your list of Lat/Long grid centroids.')
    

def makebelieve(homedir, mappingfile, BiasCorr, metadata, start_catalog_label, end_catalog_label, 
                file_start_date=None, file_end_date=None,
                data_dir=None, dest_dir_suffix=None):
    np.set_printoptions(precision=6)

    # take liv2013 date set date range as default if file reference dates are not given
    if isinstance(file_start_date, type(None)):
        file_start_date = metadata[start_catalog_label]['date_range']['start']

    if isinstance(file_end_date, type(None)):
        file_end_date = metadata[start_catalog_label]['date_range']['end']

    # generate the month vector
    month = pd.date_range(start=file_start_date, end=file_end_date).month
    month = pd.DataFrame({'month':month})

    # create NEW directory
    if isinstance(dest_dir_suffix, type(None)):
        dest_dir_suffix = 'biascorr_output/'

    dest_dir = os.path.join(homedir, dest_dir_suffix)
    if not os.path.exists(dest_dir):
        os.mkdir(dest_dir)
        print('destdir created')

    # read in the mappingfile
    map_df, nstations = mappingfileToDF(mappingfile, colvar='all')

    # compile the BiasCorr dictionary into a pandas panel
    BiasCorr=pd.Panel.from_dict(BiasCorr)

    # loop through each file
    for ind, eachfile in enumerate(map_df.loc[:,start_catalog_label]):
        
        # identify the file
        station = map_df.loc[map_df.loc[:,start_catalog_label]==eachfile,['FID', 'LAT', 'LONG_']].reset_index(drop=True)

        # subset the bias correction to the file at hand
        print(str(ind)+' station: '+str(tuple(station.loc[0,:])))
        BiasCorr_df = BiasCorr.xs(key=tuple(station.loc[0,:]),axis=2)
                
        # read in the file to be corrected
        read_dat = pd.read_table(eachfile, delimiter=metadata[start_catalog_label]['delimiter'],
                                 header=None, names=metadata[start_catalog_label]['variable_list'])
        
        # extrapolate monthly values for each variable
        for eachvar in read_dat.columns:
                        
            # identify the corresponding bias correction key
            for eachkey in BiasCorr_df.columns:
                if eachkey.startswith(eachvar):
                                
                    # subset the dataframe to the variable in loop
                    BiasCorr_subdf = BiasCorr_df.loc[:,eachkey]

                    # regenerate row index as month column
                    BiasCorr_subdf = BiasCorr_subdf.reset_index().rename(columns={'index':'month'})

                    # generate the s-vector
                    s = month.merge(BiasCorr_subdf, how='left', on='month').loc[:,eachkey]

                    if eachvar=='PRECIP':
                        #Use for ratio precip method
                        read_dat[eachvar] = np.multiply(np.array(read_dat.loc[:,eachvar]), np.array(s))

                        #read_dat[eachvar] = np.array(read_dat.loc[:,eachvar])+np.array(s)
                        #positiveprecip=read_dat[eachvar]
                        #positiveprecip[positiveprecip<0.]=0.
                        #read_dat[eachvar] = positiveprecip*.9842
                    else:
                        read_dat[eachvar] = np.array(read_dat.loc[:,eachvar])+np.array(s)

        # write it out to the new destination location
        read_dat.to_csv(os.path.join(dest_dir, os.path.basename(eachfile)), sep='\t', header=None, index=False, float_format='%.4f')

    # update the mappingfile with the file catalog
    addCatalogToMap(outfilepath=mappingfile, maptable=map_df, folderpath=dest_dir, catalog_label=end_catalog_label)

    # append the source metadata to the new catalog label metadata
    metadata[end_catalog_label] = metadata[start_catalog_label]
    print('mission complete. this device will now self-destruct.')
    print('just kidding.')

    return(dest_dir, metadata)


def plot_meanP(dictionary, loc_name, start_date, end_date):
    # Plot 1: Monthly temperature analysis of Livneh data
    if 'meanmonth_precip_liv2013_met_daily' and 'meanmonth_precip_wrf2014_met_daily' not in dictionary.keys():
        pass

    # generate month indices
    wy_index=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    wy_numbers=[10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    month_strings=[ 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept']
    
    # initiate the plot object
    fig, ax=plt.subplots(1,1,figsize=(10, 6))

    if 'meanmonth_precip_liv2013_met_daily' in dictionary.keys():
        # Liv2013

        plt.plot(wy_index, dictionary['meanmonth_precip_liv2013_met_daily'][wy_numbers],'r-', linewidth=1, label='Liv Precip')  
    
    if 'meanmonth_precip_wrf2014_met_daily' in dictionary.keys():
        # WRF2014
        plt.plot(wy_index, dictionary['meanmonth_precip_wrf2014_met_daily'][wy_numbers],'b-',linewidth=1, label='WRF Precip')
 
    if 'meanmonth_precip_livneh2013_wrf2014bc_met_daily' in dictionary.keys():
        # WRF2014
        plt.plot(wy_index, dictionary['meanmonth_precip_livneh2013_wrf2014bc_met_daily'][wy_numbers],'g-',linewidth=1, label='WRFbc Precip')
 
    # add reference line at y=0
    plt.plot([1, 12],[0, 0], 'k-',linewidth=1)

    plt.ylabel('Precip (mm)',fontsize=14)
    plt.xlabel('Month',fontsize=14)
    plt.xlim(1,12);
    plt.xticks(wy_index, month_strings);
        
    plt.tick_params(labelsize=12)
    plt.legend(loc='best')
    plt.grid(which='both')
    plt.title(str(loc_name)+'\nAverage Precipitation\n Years: '+str(start_date.year)+'-'+str(end_date.year)+'; Elevation: '+str(dictionary['analysis_elev_min'])+'-'+str(dictionary['analysis_elev_max'])+'m', fontsize=16)
    plt.savefig('monthly_precip'+str(loc_name)+'.png')
    plt.show()
    
def plot_meanTavg(dictionary, loc_name, start_date, end_date):
    # Plot 1: Monthly temperature analysis of Livneh data
    if 'meanmonth_temp_avg_liv2013_met_daily' and 'meanmonth_temp_avg_wrf2014_met_daily' not in dictionary.keys():
        pass

    # generate month indices
    wy_index=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    wy_numbers=[10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    month_strings=[ 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept']
    
    # initiate the plot object
    fig, ax=plt.subplots(1,1,figsize=(10, 6))

    if 'meanmonth_temp_avg_liv2013_met_daily' in dictionary.keys():
        # Liv2013

        plt.plot(wy_index, dictionary['meanmonth_temp_avg_liv2013_met_daily'][wy_numbers],'r-', linewidth=1, label='Liv Temp Avg')  
    
    if 'meanmonth_temp_avg_wrf2014_met_daily' in dictionary.keys():
        # WRF2014
        plt.plot(wy_index, dictionary['meanmonth_temp_avg_wrf2014_met_daily'][wy_numbers],'b-',linewidth=1, label='WRF Temp Avg')
 
    if 'meanmonth_temp_avg_livneh2013_wrf2014bc_met_daily' in dictionary.keys():
        # WRF2014
        plt.plot(wy_index, dictionary['meanmonth_temp_avg_livneh2013_wrf2014bc_met_daily'][wy_numbers],'g-',linewidth=1, label='WRFbc Temp Avg')
 
    # add reference line at y=0
    plt.plot([1, 12],[0, 0], 'k-',linewidth=1)

    plt.ylabel('Temp (C)',fontsize=14)
    plt.xlabel('Month',fontsize=14)
    plt.xlim(1,12);
    plt.xticks(wy_index, month_strings);
        
    plt.tick_params(labelsize=12)
    plt.legend(loc='best')
    plt.grid(which='both')
    plt.title(str(loc_name)+'\nAverage Temperature\n Years: '+str(start_date.year)+'-'+str(end_date.year)+'; Elevation: '+str(dictionary['analysis_elev_min'])+'-'+str(dictionary['analysis_elev_max'])+'m', fontsize=16)
    plt.savefig('monthly_Tavg'+str(loc_name)+'.png')
    plt.show()
    
def plot_meanTmin(dictionary, loc_name, start_date, end_date):
    # Plot 1: Monthly temperature analysis of Livneh data
    if 'meanmonth_temp_min_liv2013_met_daily' and 'meanmonth_temp_min_wrf2014_met_daily' not in dictionary.keys():
        pass

    # generate month indices
    wy_index=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    wy_numbers=[10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    month_strings=[ 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept']
    
    # initiate the plot object
    fig, ax=plt.subplots(1,1,figsize=(10, 6))

    if 'meanmonth_temp_min_liv2013_met_daily' in dictionary.keys():
        # Liv2013

        plt.plot(wy_index, dictionary['meanmonth_temp_min_liv2013_met_daily'][wy_numbers],'r-', linewidth=1, label='Liv Temp min')  
    
    if 'meanmonth_temp_min_wrf2014_met_daily' in dictionary.keys():
        # WRF2014
        plt.plot(wy_index, dictionary['meanmonth_temp_min_wrf2014_met_daily'][wy_numbers],'b-',linewidth=1, label='WRF Temp min')
 
    if 'meanmonth_temp_min_livneh2013_wrf2014bc_met_daily' in dictionary.keys():
        # WRF2014
        plt.plot(wy_index, dictionary['meanmonth_temp_min_livneh2013_wrf2014bc_met_daily'][wy_numbers],'g-',linewidth=1, label='WRFbc Temp min')
 
    # add reference line at y=0
    plt.plot([1, 12],[0, 0], 'k-',linewidth=1)

    plt.ylabel('Temp (C)',fontsize=14)
    plt.xlabel('Month',fontsize=14)
    plt.xlim(1,12);
    plt.xticks(wy_index, month_strings);
        
    plt.tick_params(labelsize=12)
    plt.legend(loc='best')
    plt.grid(which='both')
    plt.title(str(loc_name)+'\nMinimum Temperature\n Years: '+str(start_date.year)+'-'+str(end_date.year)+'; Elevation: '+str(dictionary['analysis_elev_min'])+'-'+str(dictionary['analysis_elev_max'])+'m', fontsize=16)
    plt.savefig('monthly_Tmin'+str(loc_name)+'.png')
    plt.show()
    
def plot_meanTmax(dictionary, loc_name, start_date, end_date):
    # Plot 1: Monthly temperature analysis of Livneh data
    if 'meanmonth_temp_max_liv2013_met_daily' and 'meanmonth_temp_max_wrf2014_met_daily' not in dictionary.keys():
        pass

    # generate month indices
    wy_index=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    wy_numbers=[10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    month_strings=[ 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept']
    
    # initiate the plot object
    fig, ax=plt.subplots(1,1,figsize=(10, 6))

    if 'meanmonth_temp_max_liv2013_met_daily' in dictionary.keys():
        # Liv2013

        plt.plot(wy_index, dictionary['meanmonth_temp_max_liv2013_met_daily'][wy_numbers],'r-', linewidth=1, label='Liv Temp max')  
    
    if 'meanmonth_temp_max_wrf2014_met_daily' in dictionary.keys():
        # WRF2014
        plt.plot(wy_index, dictionary['meanmonth_temp_max_wrf2014_met_daily'][wy_numbers],'b-',linewidth=1, label='WRF Temp max')
 
    if 'meanmonth_temp_max_livneh2013_wrf2014bc_met_daily' in dictionary.keys():
        # WRF2014
        plt.plot(wy_index, dictionary['meanmonth_temp_max_livneh2013_wrf2014bc_met_daily'][wy_numbers],'g-',linewidth=1, label='WRFbc Temp max')
 
    # add reference line at y=0
    plt.plot([1, 12],[0, 0], 'k-',linewidth=1)

    plt.ylabel('Temp (C)',fontsize=14)
    plt.xlabel('Month',fontsize=14)
    plt.xlim(1,12);
    plt.xticks(wy_index, month_strings);
        
    plt.tick_params(labelsize=12)
    plt.legend(loc='best')
    plt.grid(which='both')
    plt.title(str(loc_name)+'\nMaximum Temperature\n Years: '+str(start_date.year)+'-'+str(end_date.year)+'; Elevation: '+str(dictionary['analysis_elev_min'])+'-'+str(dictionary['analysis_elev_max'])+'m', fontsize=16)
    plt.savefig('monthly_Tmax'+str(loc_name)+'.png')
    plt.show()
    
    
def renderWatershed(shapefile, outfilepath):
    fig = plt.figure(figsize=(10,5), dpi=1000)
    ax1 = plt.subplot2grid((1,1),(0,0))

    # generate the polygon color-scheme
    cmap = mpl.cm.get_cmap('coolwarm')
    norm = mpl.colors.Normalize(0, 1)
    color_producer = mpl.cm.ScalarMappable(norm=norm, cmap=cmap)

    # calculate bounding box based on the watershed shapefile
    watershed = fiona.open(shapefile)
    minx, miny, maxx, maxy = watershed.bounds
    w, h = maxx - minx, maxy - miny

    # watershed
    ptchs=[PolygonPatch(shape(pol['geometry']), fc='pink', ec='pink', linewidth=0) for pol in watershed]
    watershed.close()

    # generate basemap
    m = Basemap(projection='merc',
                ellps='WGS84',
                epsg=4326,
                llcrnrlon=minx - 1 * w,
                llcrnrlat=miny - 1 * h,
                urcrnrlon=maxx + 1 * w,
                urcrnrlat=maxy + 1 * h,
                resolution='l',
                ax=ax1)
    m.arcgisimage(service='World_Physical_Map', xpixels=10000)

    # generate the collection of Patches
    coll = PatchCollection(ptchs, cmap=cmap, match_original=True)
    ax1.add_collection(coll)
    coll.set_alpha(0.4)

    # save image
    plt.savefig(outfilepath)
    plt.show()
    

def renderPointsInShape(shapefile, NAmer, mappingfile, colvar=['livneh2013_MET','Salathe2014_WRFraw'], outfilepath='oghcat_Livneh_Salathe.png', epsg=4326):

    fig = plt.figure(figsize=(10,5), dpi=1000)
    ax1 = plt.subplot2grid((1,1),(0,0))

    # generate the polygon color-scheme
    cmap = mpl.cm.get_cmap('coolwarm')
    norm = mpl.colors.Normalize(0, 1)
    color_producer = mpl.cm.ScalarMappable(norm=norm, cmap=cmap)

    # calculate bounding box based on the watershed shapefile
    watershed = fiona.open(shapefile)
    minx, miny, maxx, maxy = watershed.bounds
    w, h = maxx - minx, maxy - miny

    # watershed
    ptchs=[]
    watershed_shade = color_producer.to_rgba(0.5)
    for pol in watershed:
        ptchs.append(PolygonPatch(shape(pol['geometry']), fc=watershed_shade, ec=watershed_shade, linewidth=0))
    watershed.close()

    # generate basemap
    m = Basemap(projection='merc', ellps='WGS84', epsg=epsg,
                llcrnrlon=minx - .5 * w, llcrnrlat=miny - .5 * h, urcrnrlon=maxx + .5 * w, urcrnrlat=maxy + .5 * h,
                resolution='l', ax=ax1)
    m.arcgisimage(service='Canvas/World_Dark_Gray_Base', xpixels=10000)

    # generate the collection of Patches
    coll = PatchCollection(ptchs, cmap=cmap, match_original=True)
    ax1.add_collection(coll)
    coll.set_alpha(0.4)

    # gridded points
    gpoints = readShapefileTable(NAmer)
    ax1.scatter(gpoints['Long'], gpoints['Lat'], alpha=0.4, c=color_producer.to_rgba(0))

    # catalog
    cat, n_stations = mappingfileToDF(mappingfile, colvar=colvar)
    ax1.scatter(cat['LONG_'], cat['LAT'], alpha=0.4, c=color_producer.to_rgba(1))

    # save image
    plt.savefig(outfilepath)
    return ax1
    

def findStationCode(mappingfile, colvar, colvalue):
    """
    mappingfile: (dir) the file path to the mappingfile, which contains the LAT, LONG_, and ELEV coordinates of interest
    colvar: (string) a column name in mappingfile
    colvalue: (value) a value that corresponds to the colvar column
    """
    mapdf = pd.read_csv(mappingfile)
    outcome = mapdf.loc[mapdf[colvar]==colvalue, :][['FID','LAT','LONG_']].set_index('FID')
    return(outcome.to_records())
