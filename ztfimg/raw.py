import warnings

import dask
import dask.array as da
import numpy as np
import pandas
from astropy.io import fits

from ztfquery import io

from .base import CCD, FocalPlane, Quadrant
from .io import get_nonlinearity_table
from .utils.tools import fit_polynome, rcid_to_ccdid_qid, rebin_arr

__all__ = ["RawQuadrant", "RawCCD", "RawFocalPlane"]


class RawQuadrant( Quadrant ):

    SHAPE_OVERSCAN = 3080, 30
    # "family"
    _CCDCLASS = "RawCCD"
    _FocalPlaneCLASS = "RawFocalPlane"
    
    def __init__(self, data=None, header=None, overscan=None):
        """ 
        See also
        --------
        from_filename: load the instance given a filename 
        from_data: load the instance given its data (and header)
        """
        # Add the overscan to the __init__
        _ = super().__init__(data=data, header=header)
        if overscan is not None:
            self.set_overscan(overscan)


    @classmethod
    def _read_overscan(cls, filepath, ext, use_dask=True, persist=False):
        """ assuming fits format. """
        from astropy.io.fits import getdata
        
        if use_dask:
            overscan = da.from_delayed(dask.delayed(getdata)(filepath, ext=ext+4),
                                            shape=cls.SHAPE_OVERSCAN, dtype="float32")
            if persist:
                overscan = overscan.persist()
        else:
            overscan = getdata(filepath, ext=ext+4)
            
        return overscan            

    @classmethod
    def from_data(cls, data, header=None, overscan=None, **kwargs):
        """ Instanciate this class given data. 
        
        Parameters
        ----------
        data: numpy.array or dask.array]
            Data of the Image.
            this will automatically detect if the data are dasked.

        header: fits.Header or dask.delayed
            Header of the image.

        overscan: 2d-array
            overscan image.

        **kwargs goes to __init__

        Returns
        -------
        class instance
        """
        # the super knows overscan thanks to the kwargs passed to __init__
        use_dask = "dask" in str( type(data))
        return super().from_data(data, header=header, overscan=overscan,
                                 **kwargs)
    
    @classmethod
    def from_filename(cls, filename, qid,
                          as_path=True,
                          use_dask=False, persist=False,
                          dask_header=False,
                          **kwargs):
        """ classmethod load an instance given an input file.

        Parameters
        ----------
        filename: str
            fullpath or filename or the file to load. This must be a raw ccd file.
            If a filename is given, set as_path=False, then ztfquery.get_file() 
            will be called to grab the file for you (and download it if necessary)
            
        qid: int
            quadrant id. Which quadrant to load from the input raw image ?
            
        as_path: bool
            Set this to true if the input file is not the fullpath but you need
            ztfquery.get_file() to look for it for you.
        
        use_dask: bool
            Should dask be used ? The data will not be loaded but delayed 
            (dask.array)

        persist: bool
            = only applied if use_dask=True =
            should we use dask's persist() on data ?

        dask_header: bool, optional
            should the header be dasked too (slows down a lot)

        **kwargs: goes to __init__()

        Returns
        -------
        class instance     
        """
        # - guessing format        
        if qid not in [1,2,3,4]:
            raise ValueError(f"qid must be 1,2, 3 or 4 {qid} given")

        if not use_dask:
            dask_header = False

        meta = io.parse_filename(filename)
        filepath = cls._get_filepath(filename, as_path=as_path, use_dask=use_dask)
        # data
        data = cls._read_data(filepath, ext=qid, use_dask=use_dask, persist=persist)
        header = cls._read_header(filepath, ext=qid, use_dask=dask_header, persist=persist)
        # and overscan
        overscan = cls._read_overscan(filepath, ext=qid, use_dask=use_dask, persist=persist)
        
        this = cls(data, header=header, overscan=overscan, **kwargs)
        this._qid = qid
        this._filename = filename
        this._filepath = filepath        
        this._meta = meta
        return this

    @classmethod
    def from_filefracday(cls, filefracday, rcid, use_dask=True, persist=False, **kwargs):
        """ load the instance given a filefracday and the rcid (ztf ID)

        Parameters
        ----------
        filefracday: str
            ztf ID of the exposure (YYYYMMDDFFFFFF) like 20220704387176
            ztfquery will fetch for the corresponding data.

        rcid: int
            rcid of the given quadrant

        use_dask: bool
            Should dask be used ? The data will not be loaded but delayed 
            (dask.array)

        persist: bool
            = only applied if use_dask=True =
            should we use dask's persist() on data ?

        **kwargs goes to from_filename -> __init__

        Returns
        -------
        class instance
        
        """
        from ztfquery.io import filefracday_to_local_rawdata
        ccdid, qid = rcid_to_ccdid_qid(rcid)
        
        filename = filefracday_to_local_rawdata(filefracday, ccdid=ccdid)
        
        if len(filename)==0:
            raise IOError(f"No local raw data found for filefracday: {filefracday} and ccdid: {ccdid}")
        
        if len(filename)>1:
            raise IOError(f"Very strange: several local raw data found for filefracday: {filefracday} and ccdid: {ccdid}", filename)
        
        return cls.from_filename(filename[0], qid=qid, use_dask=use_dask,
                                 as_path=False, persist=persist, **kwargs)
    
    @staticmethod
    def read_rawfile_header(filepath, qid, grab_imgkeys=True):
        """ reads the filename's header and returns it as a pandas.DataFrame

        Parameters
        ----------
        filepath: str
            path of the data file.

        qid: int
            quadrant id for the header you want.

        grab_imgkeys: bool
            should the gobal image header data also be included
            (i.e. header from both ext=0 and ext=qid

        Returns
        -------
        `pandas.DataFrame`
            the header
        """
        imgkeys = ["EXPTIME", "IMGTYPE", "PIXSCALE", "THETA_X", "THETA_Y", "INST_ROT", 
                   "FILTER", "OBSJD", "RAD", "DECD", "TELRA","TELDEC", "AZIMUTH","ELVATION",
                   ]
        
        if "_f.fits" in filepath:
            imgkeys += ["ILUM_LED", "ILUMWAVE", "ILUMPOWR"]

        header  = fits.getheader(filepath, ext=qid)
        if grab_imgkeys:
            imgheader = fits.getheader(filepath, ext=0)
            for key in imgkeys:
                header.set(key, imgheader.get(key), imgheader.comments[key])
            
        del imgheader
        # DataFrame to be able to dask it.
        return pandas.DataFrame( pandas.Series(header) )

    # -------- #
    #  SETTER  #
    # -------- #
    def set_overscan(self, overscan):
        """ set the overscan image.
        
        = It is unlikely you need to use that directly. =
        
        Parameters
        ----------
        overscan: 2d-array
            overscan 2d-image

        Returns
        -------
        None
        """
        self._overscan = overscan
                    
    # -------- #
    # GETTER   #
    # -------- #
    def get_data_and_overscan(self, stacked=True):
        """ hstack of data and oversan with amplifier at (0,0)

        If stacked, the resulting shape is:
           (quad.SHAPE[0], quad.SHAPE[1]+quad.SHAPE_OVERSCAN[1])

        Parameters
        ----------
        stacked: bool
            Format of the returned data: 
            - True: np.hstack([data, overscan]) 
            - False: [data, overscan]
            
        Returns
        data and overscan
            array, (see stacked)
        """
        # force reorder to False as this is sorted later on.
        data = self._reorder_data(self.data, in_="raw", out_="read")
        overscan = self._reorder_data(self.overscan, in_="raw", out_="read")
        if stacked:
            return self._np_backend.hstack([data, overscan])
            
        return data, overscan

    def _reorder_data(self, data, in_="raw", out_="sky"):
        """ 
        in_ and out_ could be:
        - raw: as stored in the raw image
        - sky: as observed in the sky
        - read: as red to the amp (0,0)= first pixel seen
        """
        if in_ == out_: # nothing to do
            return data
        
        # raw <-> sky
        elif (in_ == "raw" and out_ == "sky") or (in_ == "sky" and out_ == "raw"):
            data = data[::-1,::-1]

        # raw <-> read            
        elif (in_ == "raw" and out_ == "read") or (in_ == "read" and out_ == "raw"):
            if self.qid in [2, 3]:
                data = data[:,::-1]

            # bottom for quadrants 3 and 4
            if self.qid in [3, 4]:
                data = data[::-1,:]
                
        elif (in_ == "sky" and out_ == "read") or (in_ == "read" and out_ == "sky"):
            data_raw = self._reorder_data(data, in_=in_, out_="raw")
            data = self._reorder_data(data_raw, in_="raw", out_=out_)
            
        else:
            warnings.warn("cannot parse the input in_ and out_, nothing happens")
            
        return data
        
        
    def get_data(self,
                     corr_overscan=False,
                     corr_nl=False,
                     corr_pocket=False,
                     rebin=None, rebin_stat="nanmean",
                     reorder=True,
                     overscan_prop={}, **kwargs):
        """ get the image data. 

        returned data can be affected by different effects.
        
        Parameters
        ----------
        corr_overscan: bool
            Should the data be corrected for overscan
            (if both corr_overscan and corr_nl are true, 
            nl is applied first)

        corr_nl: bool
            Should data be corrected for non-linearity

        corr_pocket: bool
            Should data be corrected for the pocket effect ?
            
        rebin: int, None
            Shall the data be rebinned by square of size `rebin` ?
            None means no rebinning.
            (see details in rebin_stat)
            rebin must be a multiple of the image shape.
            for instance if the input shape is (6160, 6144)
            rebin could be 2,4,8 or 16

        rebin_stat: str
            = applies only if rebin is not None =
            numpy (dask.array) method used for rebinning the data.
            For instance, if rebin=4 and rebin_stat = median
            the median of a 4x4 pixel will be used to form a new pixel.
            The dimension of the final image will depend on rebin.
        
        reorder: bool
            Should the data be re-order to match the actual north-up.
            (leave to True if not sure)
            
        overscan_prop: [dict] -optional-
            kwargs going to get_overscan()
            - > e.g. userange=[10,20], stackstat="nanmedian", modeldegree=5,
            
        Returns
        -------
        2d-array
            numpy or dask array
        """
        format_ = "raw" # ordering format

        # ------------ #
        # Data access  #
        # ------------ #
        
        # rebin is made later on.
        if not corr_pocket:
            data_ = super().get_data(rebin=None, reorder=False, **kwargs)
            
        else: # pocket effect need to overscan attached | this changes the "format".
            data_ = self.get_data_and_overscan(stacked=True)
            format_ = "read" # ordering format
            
        # ------------ #
        # processing   #
        # ------------ #
        # remove overscan
        if corr_overscan:
            os_model = self.get_overscan( **{**dict(which="model"), **overscan_prop} )
            data_ -= os_model[:,None]            

        # correct non-linearity                        
        if corr_nl:
            a, b = self.get_nonlinearity_corr()
            data_ /= (a*data_**2 + b*data_ + 1)


        # Correction for the pocket effect
        if corr_pocket:
            from ztfsensors import pocket
            from ztfsensors.correct import correct_pixels

            if not corr_overscan or not corr_nl:
                warnings.warn("pocket effect correction is expected to happend post overscan and nl correction")

            # make sure this in "read" format as requested by PocketModel
            # _reorder_data will do nothing if in_ == out_
            data_ = self._reorder_data(data_, in_=format_, out_="read") # make sure it is the good format
            format_ = "read"
            n_overscan = self.overscan.shape[1] # overscan pixels
            
            pockelconfig = pocket.get_config(self.ccdid, self.qid).values[0]
            pockemodel = pocket.PocketModel(**pockelconfig)
            data_and_overscan = correct_pixels(pockemodel, data_, n_overscan=n_overscan)
            data_ = data_and_overscan[:,:-n_overscan]
        
        # ------------ #
        #  Formating   #
        # ------------ #
        if rebin is not None:
            data_ = getattr(self._np_backend, rebin_stat)(
                rebin_arr(data_, (rebin,rebin), use_dask=True), axis=(-2,-1) )

        # make sure it is re-ordered
        if reorder:
            data_ = self._reorder_data(data_, in_=format_, out_="sky")

        return data_

    def get_nonlinearity_corr(self):
        """ looks in the raw.NONLINEARITY_TABLE the the entry corresponding to the quadrant's rcid
        and returns the a and b parameters. 
        
        raw data should be corrected as such:
        ```
        data_corr = data/(a*data**2 + b*data +1)
        ```

        Return
        ------
        data
        """
        obsdate = np.datetime64('-'.join(self.meta[['year', 'month', 'day']].values))
        NONLINEARITY_TABLE = get_nonlinearity_table(obsdate)

        return NONLINEARITY_TABLE.loc[self.rcid][["a","b"]].astype("float").values
        
    def get_overscan(self, which="data", sigma_clipping=3,
                         userange=[20,30], stackstat="nanmedian",
                         modeldegree=3, specaxis=1,
                         corr_overscan=False, corr_nl=False):
        """ 
        
        Parameters
        ----------
        which: str
            There are different format. 
            - data and raw are 2d images:
               - 'raw' are the overscan as stored
               - 'data' re-order the data, that is, first overscan left and north up 
                        matching get_data(reorder=True).

            could be:
              - 'raw': as stored | 
                      most likely you want 'data' as it includes re-ordering 
                      i.e. [:,0] is the first overscan independently of the quadrant
              - 'data': raw re-ordered and within userange (if given). 
              - 'spec': vertical or horizontal profile of the overscan
              see stackstat (see specaxis). Clipping is applied at that time (if clipping=True)
              - 'model': polynomial model of spec
            
        clipping: bool
            Should clipping be applied to remove the obvious flux excess.
            This clipping is made using median statistics (median and 3*nmad)
            
        specaxis: int
            axis along which you are doing the median 
            = Careful: after userange applied = 
            - axis: 1 (default) -> horizontal overscan data spectrum (~3000 pixels)
            - axis: 0 -> vertical stack of the overscan (~30 pixels)
            (see stackstat for stacking statistic (mean, median etc)
            
        stackstat: str
            numpy method to use to converting data into spec
            
        userange: 2d-array
            = ignored is which != data or raw =
            start and end of overscan data to be considered. 
                        
        corr_overscan: bool
            = only if which is raw or data = 
            Should the data be corrected for overscan
            (if both corr_overscan and corr_nl are true, 
            nl is applied first)

        corr_nl: bool
            = only if which is raw or data = 
            Should data be corrected for non-linearity

        Returns
        -------
        1 or 2d array (see which)

        Examples
        --------
        To get the raw overscan vertically stacked spectra, using mean statistic do:
        get_overscan('spec', userange=None, specaxis=0, stackstat='nanmean')
        """
            
        # raw (or data, that is cleaned raw)            
        if which in ["raw", "data"]:
            data = self.overscan.copy()
            if which == "data":
                # left-right inversion
                # first overscan is self.get_overscan('data')[:,0]
                if self.qid in [2, 3]:
                    data = data[::-1, ::-1]
                else:
                    data = data[::-1, :]
                    
            if userange is not None:
                data = data[:, userange[0]:userange[1]]
                    
            # correct for non linearity
            if corr_nl:
                a, b = self.get_nonlinearity_corr()
                data /= (a*data**2 + b*data + 1)
                
            # correct for overscan model
            if corr_overscan:
                os_model = self.get_overscan(which="model")
                data -= os_model[:,None]            
                
            return data

        # Spectrum or Model
        if which == "spec":
            # data means re-ordering applied so 0 is first overscan column.
            data = self.get_overscan(which="data", userange=userange)
            return self._get_overscan_spec_(data,
                                            sigma_clipping=sigma_clipping,
                                            stackstat=stackstat,
                                            axis=specaxis)
        
        if which == "model":
            spec = self.get_overscan(which = "spec", userange=userange,
                                        sigma_clipping=sigma_clipping,                                         
                                        stackstat=stackstat,
                                        specaxis=specaxis)
            # dask
            if self._use_dask:                
                d_ = dask.delayed(fit_polynome)(np.arange( len(spec) ), spec, degree=modeldegree)
                return da.from_delayed(d_, shape=spec.shape, dtype="float32")
            # numpy
            return fit_polynome(np.arange(len(spec)), spec, degree=modeldegree)
        
        raise ValueError(f'which should be "raw", "data", "spec", "model", {which} given')    

    @classmethod
    def _get_overscan_spec_(cls, data, 
                            sigma_clipping=None, stackstat="nanmedian", 
                            axis=1):
        """ compute the overscan spectrum from a input overscan 2d-data

        Parameters
        ----------
        data: array
            2d dask or numpy array

        sigma_clipping: float
            sigma for the clipping (median statistics used).
            None or 0 means no clipping.

        stackstat: str
            numpy method to go from 2d to 1d array. data->spec

        axes: int
            stacking axis. 1 means overscan spectrum.

        Returns
        -------
        1d-array
            numpy or dask depending on input data.
        """

        if "dask" in str( type(data) ):
            d_spec = dask.delayed(cls._get_overscan_spec_)(data,
                                                        sigma_clipping=sigma_clipping, 
                                                        stackstat=stackstat,
                                                        axis=axis)

            new_shape = list(data.shape)
            _ = new_shape.pop(axis)
            new_shape = tuple(new_shape)
            
            spec = da.from_delayed(d_spec, shape=new_shape, dtype=data.dtype)
            return spec


        try:
            from scipy.stats import median_abs_deviation as nmad  # scipy>1.9
        except:
            from scipy.stats import median_absolute_deviation as nmad  # scipy<1.9

        # numpy based
        spec = getattr(np,stackstat)(data, axis=axis)
        med_ = np.median( spec, axis=0)
        if sigma_clipping is not None and sigma_clipping>0:
            mad_  = nmad( spec, axis=0)
            # Symmetric to avoid bias, even though only positive outlier are expected.
            flag_out = (spec>(med_+sigma_clipping*mad_)) +(spec<(med_-sigma_clipping*mad_))
            spec[flag_out] = np.nan

        return spec

    def get_lastdata_firstoverscan(self, n=1, corr_overscan=False, corr_nl=False, **kwargs):
        """ get the last data and the first overscan columns
        
        Parameters
        ----------
        n: int
           n-last and n-first

        **kwargs goes to get_data

        Returns
        -------
        list 
            (2, n-row) data (last_data, first_overscan)
        """
        # for reorder to make sure they are on the "normal" way.
        data = self.get_data(reorder=True, corr_overscan=corr_overscan, corr_nl=corr_nl,
                            **kwargs)
        # raw means no change in ordering etc.
        overscan = self.get_overscan("raw", corr_overscan=corr_overscan, corr_nl=corr_nl)
        # reminder
        # q2 | q1
        # -------
        # q3 | q4
        
        if self.qid in [1,4]: # top-right, bottom-righ
            last_data = data[:,:n] # 0 = leftmost part of the ccd
            first_overscan = overscan[:,:n]
        else: # top-left, bottom-left
            # [:,::-1] means first, second, etc.
            last_data = data[:,-n:][:,::-1] # rightmost part of the ccd
            first_overscan = overscan[:,-n:][:,::-1]
            
        return last_data.squeeze(), first_overscan.squeeze()

    
    def get_sciimage(self, use_dask=None, **kwargs):
        """ get the Science image corresponding to this raw image
        
        This uses ztfquery to parse the filename and set up the correct 
        science image filename path.

        Parameters
        ----------
        use_dask: bool or None
            if None, this will use self.use_dask.

        **kwargs goes to ScienceQuadrant.from_filename

        Returns
        -------
        ScienceQuadrant
        """
        if use_dask is None:
            use_dask = self.use_dask
            
        from ztfquery.buildurl import get_scifile_of_filename

        from .science import ScienceQuadrant
        # 
        filename = get_scifile_of_filename(self.filename, qid=self.qid, source="local")
        return ScienceQuadrant.from_filename(filename, use_dask=use_dask, as_path=False, **kwargs)
    
    # -------- #
    # PLOTTER  #
    # -------- #
    def show_overscan(self, ax=None, axs=None, axm=None,
                          which="data",
                      colorbar=False, cax=None, **kwargs):
        """ display the overscan image.

        Parameters
        ----------

        Returns
        -------
        fig
        """
        import matplotlib.pyplot as mpl
        
        if ax is None:
            fig = mpl.figure(figsize=[4,6])
            ax  = fig.add_axes([0.15, 0.100, 0.58, 0.75])
            axs = fig.add_axes([0.75, 0.100, 0.20, 0.75])
            axm = fig.add_axes([0.15, 0.865, 0.58, 0.10])
        else:
            fig = ax.figure
            
        prop = dict(origin="lower", cmap="cividis", aspect="auto")
        im = ax.imshow(self.get_overscan(which), **{**prop,**kwargs})
        
        if axs is not None:
            spec = self.get_overscan("spec")
            model = self.get_overscan("model")
            axs.plot(spec, np.arange(len(spec)))
            axs.plot(model, np.arange(len(spec)))
            axs.set_yticks([])
            axs.set_ylim(*ax.get_ylim())
        
        if axm is not None:
            spec_to = self.get_overscan("spec", userange=None, specaxis=0)
            axm.plot(np.arange(len(spec_to)), spec_to)
            axm.set_xticks([])
            axm.set_xlim(*ax.get_xlim())
            
        if colorbar:
            fig.colorbar(im, cax=cax, ax=ax)
            
        return fig
        
    # =============== #
    #  Properties     #
    # =============== #
    @property
    def _np_backend(self):
        """ """
        return da if self._use_dask else np
    
    @property
    def shape_overscan(self):
        """ shape of the raw overscan data """
        return self.SHAPE_OVERSCAN
    
    @property
    def overscan(self):
        """ """
        if not hasattr(self, "_overscan"):
            return None
        return self._overscan
            
    @property
    def qid(self):
        """ quadrant (amplifier of the ccd) id (1->4) """
        return self._qid if hasattr(self, "_qid") else (self.get_value("AMP_ID")+1)
    
    @property
    def rcid(self):
        """ quadrant (within the focal plane) id (0->63) """
        return 4*(self.ccdid - 1) + self.qid - 1
    
    @property
    def gain(self):
        """ gain [adu/e-] """
        return self.get_value("GAIN", np.nan, attr_ok=False) # avoid loop

    @property
    def darkcurrent(self):
        """ Dark current [e-/s]"""
        return self.get_value("DARKCUR", None, attr_ok=False) # avoid loop
    
    @property
    def readnoise(self):
        """ read-out noise [e-] """
        return self.get_value("READNOI", None, attr_ok=False) # avoid loop
        
        
class RawCCD( CCD ):

    _COLLECTION_OF = RawQuadrant
    # "family"
    _QUADRANTCLASS = "RawQuadrant"
    _FocalPlaneCLASS = "RawFocalPlane"    
    
    
    @classmethod
    def from_filename(cls, filename, as_path=True, use_dask=False, persist=False, **kwargs):
        """ load the instance from the raw filename.

        Parameters
        ----------
        filename: str
            fullpath or filename or the file to load.
            If a filename is given, set as_path=False,  then ztfquery.get_file() 
            will be called to grab the file for you (and download it if necessary)
            
        as_path: bool
            Set this to true if the input file is not the fullpath but you need
            ztfquery.get_file() to look for it for you.
        
        use_dask: bool, optional
            Should dask be used ? The data will not be loaded but delayed 
            (dask.array)

        persist: bool, optional
            = only applied if use_dask=True =
            should we use dask's persist() on data ?

        **kwargs: goes to _QUADRANTCLASS.from_filename

        
        Returns
        -------
        class instance 
        
        Examples
        --------
        Load a ztf image you know the name of but not the full path.
        
        >>> rawccd = ztfimg.RawCCD.from_filename("ztf_20220704387176_000695_zr_c11_o.fits.fz", as_path=False)
        """
        qids = (1,2,3,4)
        
        quadrant_from_filename = cls._quadrantclass.from_filename            
        quadrants = [quadrant_from_filename(filename,
                                            qid=qid,
                                            as_path=as_path,
                                            use_dask=use_dask,
                                            persist=persist,
                                            **kwargs)
                         for qid in qids]
            
        this = cls.from_quadrants(quadrants, qids=qids)
        this._filename = filename
        if as_path:
            this._filepath = filename
            
        this._meta = io.parse_filename(filename)
        return this

    @classmethod
    def from_single_filename(cls, *args, **kwargs):
        """ rawccd data have a single file. 

        See also
        --------
        from_filename: load the instance given the raw filename
        """
        return cls.from_filename(*args, **kwargs)
    
    @classmethod
    def from_filenames(cls, *args, **kwargs):
        """ rawccd data have a single file. 

        See also
        --------
        from_filename: load the instance given the raw filename
        """
        raise NotImplementedError("from_filenames does not exists. See from_filename")
    
    @classmethod
    def from_filefracday(cls, filefracday, ccdid, use_dask=True, **kwargs):
        """ load the instance given a filefracday and the ccidid (ztf ID)

        Parameters
        ----------
        filefracday: str
            ztf ID of the exposure (YYYYMMDDFFFFFF) like 20220704387176
            ztfquery will fetch for the corresponding data.

        ccidid: int
            ccidid of the given ccd

        use_dask: bool
            Should dask be used ? The data will not be loaded but delayed 
            (dask.array)

        persist: bool
            = only applied if use_dask=True =
            should we use dask's persist() on data ?

        **kwargs goes to from_filename -> __init__

        Returns
        -------
        class instance
        
        """
        from ztfquery.io import filefracday_to_local_rawdata
        filename = filefracday_to_local_rawdata(filefracday, ccdid=ccdid)
        if len(filename)==0:
            raise IOError(f"No local raw data found for filefracday: {filefracday} and ccdid: {ccdid}")
        if len(filename)>1:
            raise IOError(f"Very strange: several local raw data found for filefracday: {filefracday} and ccdid: {ccdid}", filename)
        
        return cls.from_filename(filename[0], as_path=False, use_dask=use_dask, **kwargs)



    def get_sciimage(self, use_dask=None, qid=None, as_ccd=True, **kwargs):
        """ get the Science image corresponding to this raw image
        
        This uses ztfquery to parse the filename and set up the correct 
        science image filename path.

        Parameters
        ----------
        use_dask: bool or None
            if None, this will use self.use_dask.
            
        qid: int or None
            do you want a specific quadrant ?
            
        as_ccd: bool
            = ignored if qid is not None =
            should this return a list of science quadrant (False)
            or a ScienceCCD (True) ?

        **kwargs goes to ScienceQuadrant.from_filename

        Returns
        -------
        ScienceQuadrant
        """
        if use_dask is None:
            use_dask = self.use_dask

        from ztfquery.buildurl import get_scifile_of_filename

        from .science import ScienceQuadrant
        # Specific quadrant
        prop = {"as_path":False}
        
        if qid is not None:
            filename = get_scifile_of_filename(self.filename, qid=qid, source="local")
            return ScienceQuadrant.from_filename(filename, use_dask=use_dask, **{**prop,**kwargs} )

        # no quadrant given -> 4 filenames (qid = 1,2,3,4)
        filenames = get_scifile_of_filename(self.filename, source="local")
        quadrants = [ScienceQuadrant.from_filename(filename, use_dask=use_dask, **{**prop,**kwargs} )
                    for filename in filenames]
        
        if as_ccd:
            from .science import ScienceCCD
            return ScienceCCD.from_quadrants(quadrants, qids=[1,2,3,4], **kwargs)
        
        # If not, then list of science quadrants
        return quadrants

    
class RawFocalPlane( FocalPlane ):
    # INFORMATION || Numbers to be fine tuned from actual observations
    # 15 µm/arcsec  (ie 1 arcsec/pixel) and using 
    # 7.2973 mm = 487 pixel gap along rows (ie between columns) 
    # and 671 pixels along columns.
    _COLLECTION_OF = RawCCD
    # family
    _CCDCLASS = "RawCCD"
    
    @classmethod
    def from_filenames(cls, filenames, as_path=True,
                           use_dask=False, persist=False,
                           **kwargs):
        """ load the instance from the raw filename.

        Parameters
        ----------
        filenames: list of str
            list of fullpath or filename or the ccd file to load.
            If a filename is given, set as_path=False,  then ztfquery.get_file() 
            will be called to grab the file for you (and download it if necessary)
            
        as_path: bool
            Set this to true if the input file is not the fullpath but you need
            ztfquery.get_file() to look for it for you.
        
        use_dask: bool, optional
            Should dask be used ? The data will not be loaded but delayed 
            (dask.array)

        persist: bool, optional
            = only applied if use_dask=True =
            should we use dask's persist() on data ?

        **kwargs: goes to _CCDCLASS.from_filename
        
        Returns
        -------
        class instance 
        
        """
        this = cls()
        for file_ in filenames:
            ccd_ = cls._ccdclass.from_filename(file_, as_path=as_path,
                                                   use_dask=use_dask, persist=persist,
                                                   **kwargs)
            this.set_ccd(ccd_, ccdid=ccd_.ccdid)

        this._filenames = filenames
        if as_path:
            this._filepaths = filenames
            
        return this

    @classmethod
    def from_filefracday(cls, filefracday, use_dask=True, **kwargs):
        """ load the instance given a filefracday and the ccidid (ztf ID)

        Parameters
        ----------
        filefracday: str
            ztf ID of the exposure (YYYYMMDDFFFFFF) like 20220704387176
            ztfquery will fetch for the corresponding data.

        use_dask: bool
            Should dask be used ? The data will not be loaded but delayed 
            (dask.array)

        persist: bool
            = only applied if use_dask=True =
            should we use dask's persist() on data ?

        **kwargs goes to from_filenames -> _CCDCLASS.from_filename

        Returns
        -------
        class instance
        
        """
        from ztfquery.io import filefracday_to_local_rawdata
        filenames = filefracday_to_local_rawdata(filefracday, ccdid="*")
        if len(filenames)==0:
            raise IOError(f"No local raw data found for filefracday: {filefracday}")
        
        if len(filenames)>16:
            raise IOError(f"Very strange: more than 16 local raw data found for filefracday: {filefracday}", filenames)
        
        if len(filenames)<16:
            warnings.warn(f"Less than 16 local raw data found for filefracday: {filefracday}")
        
        return cls.from_filenames(filenames, use_dask=use_dask, **kwargs)
